from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import json
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch

from baidu_index_tool.api import BaiduIndexClient, NoDataError, RequestBlockedError, ResponseFormatError, parse_news_payload
from baidu_index_tool.cli import main
from baidu_index_tool.export import write_csv, write_news_csv, write_brand_csv
from baidu_index_tool.models import BrandEntity, BrandIndexPoint
from baidu_index_tool.regions import resolve_region
from baidu_index_tool.service import query_and_export
from baidu_index_tool.task_logic import create_run_directory
from test_no_data import api_for, config, point


class ReviewTests(unittest.TestCase):
    def test_city_selection_includes_municipalities_without_changing_area_types(self):
        from baidu_index_tool.regions import all_regions, is_city_region
        regions = all_regions()
        selected = [r for r in regions if is_city_region(r)]
        self.assertEqual(len(selected), 362)
        self.assertEqual(len({r.area_id for r in selected}), 362)
        municipalities = [r for r in selected if r.kind == "province"]
        self.assertEqual({r.name for r in municipalities}, {"北京", "上海", "天津", "重庆"})
        payload = api_for(None).bootstrap()["regions"]
        self.assertEqual({r["area_id"] for r in payload if r["selectAsCity"]}, {r.area_id for r in selected})

    def test_cleared_regions_do_not_query_national_data(self):
        calls = []
        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append(region.area_id)
                return [point(keyword, start, region)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "请选择"):
                api_for(Client()).query(config(directory, regionIds=[]))
        self.assertEqual(calls, [])

    def test_duplicate_conditions_are_normalized_before_counting(self):
        calls = []
        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append((keyword, region.area_id))
                return [point(keyword, start, region)]
        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory, keywords=["供应链", " 供应链 "], regionIds=[911, 911]))
            self.assertEqual((result["completed"], result["total"], result["rowCount"]), (1, 1, 1))
        self.assertEqual(calls, [("供应链", 911)])

    def test_invalid_channels_periods_and_intervals_do_not_send_requests(self):
        class Client:
            def query_keyword(self, *args):
                raise AssertionError("invalid configuration reached network")
        values = [{"searchChannels": ["bad"]}, {"searchChannels": "overall"}, {"period": "bad"},
                  {"interval": -1}, {"interval": float("nan")}, {"interval": float("inf")},
                  {"keywords": "供应链"}, {"regionIds": "911"}]
        with tempfile.TemporaryDirectory() as directory:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    api_for(Client()).query(config(directory, **value))

    def test_same_second_queries_do_not_overwrite_prior_export(self):
        with tempfile.TemporaryDirectory() as directory:
            now = dt.datetime(2026, 10, 14, 0, 0, 0)
            first, stamp1 = create_run_directory(pathlib.Path(directory), now)
            file = first / "result.csv"
            file.write_text("ORIGINAL", encoding="utf-8")
            second, stamp2 = create_run_directory(pathlib.Path(directory), now)
            (second / "result.csv").write_text("NEW", encoding="utf-8")
            self.assertNotEqual(first, second)
            self.assertEqual(file.read_text(encoding="utf-8"), "ORIGINAL")
            self.assertEqual(stamp1, stamp2)

    def test_unmatched_brand_is_cached_and_other_brands_continue(self):
        calls = []
        events = []
        class Client:
            def resolve_brand(self, keyword):
                calls.append(keyword)
                if keyword == "不存在的品牌":
                    raise NoDataError("未匹配到品牌")
                return BrandEntity(keyword, "FIXTURE", "测试", "品牌")
            def query_brand_metric(self, entity, metric, start, end, region):
                return [BrandIndexPoint(entity.name, start, *region.csv_location, 10, None, None, None)]
        with tempfile.TemporaryDirectory() as directory:
            api = api_for(Client())
            api._emit = lambda name, data: events.append((name, data))
            settings = config(directory, indexMode="brand", keywords=["不存在的品牌", "有效品牌"],
                              regionIds=[0, 911], brandChannels=["brand_index"])
            result = api.query(settings)
            self.assertFalse(result["incomplete"])
            self.assertEqual((result["completed"], result["dataCount"], result["noDataCount"], result["rowCount"]), (4, 2, 2, 2))
            self.assertEqual(calls, ["不存在的品牌", "有效品牌"])
            progress = [data for name, data in events if name == "progress"][-1]
            self.assertEqual(progress["done"], progress["total"])
            api.query(dict(settings, resume=True))
            self.assertEqual(len(calls), 2)

    def test_brand_matching_block_is_not_treated_as_no_data(self):
        class Client:
            def resolve_brand(self, keyword):
                raise RequestBlockedError("request block")
        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory, indexMode="brand", brandChannels=["brand_index"]))
            self.assertTrue(result["blocked"])
            self.assertEqual(result["noDataCount"], 0)

    def test_null_brand_metric_is_missing_and_zero_is_data(self):
        entity = BrandEntity("测试", "FIXTURE", "行业", "品牌")
        class Transport:
            def __init__(self, value): self.value = value
            def post_json(self, *args):
                return {"status": 0, "data": [{"statDate": "2026-10-10", "value": self.value}]}
        for missing in [None, ""]:
            with self.subTest(missing=missing), self.assertRaises(NoDataError):
                BaiduIndexClient("BDUSS=FIXTURE", transport=Transport(missing)).query_brand_metric(entity, "brand_index", "2026-10-10", "2026-10-12")
        rows = BaiduIndexClient("BDUSS=FIXTURE", transport=Transport(0)).query_brand_metric(entity, "brand_index", "2026-10-10", "2026-10-12")
        self.assertEqual(rows[0].brand_index, 0)

    def test_malformed_feed_entries_are_not_no_data(self):
        for value in [None, "bad", {}, {"startDate": "2026-10-10"}]:
            with self.subTest(value=value), self.assertRaises(ResponseFormatError):
                parse_news_payload({"data": {"index": [value]}}, "FIXTURE", "测试", resolve_region(0))

    def test_malformed_brand_entries_are_not_silently_dropped(self):
        entity = BrandEntity("测试", "FIXTURE", "行业", "品牌")
        for value in [None, {}, {"statDate": "bad", "value": 10}, {"statDate": "2026-10-10", "value": {"bad": 1}},
                      {"statDate": "2026-10-10", "value": "NaN"}, {"statDate": "2026-10-10", "value": float("inf")}]:
            class Transport:
                def post_json(self, *args): return {"status": 0, "data": [value]}
            with self.subTest(value=value), self.assertRaises(ResponseFormatError):
                BaiduIndexClient("BDUSS=FIXTURE", transport=Transport()).query_brand_metric(entity, "brand_index", "2026-10-10", "2026-10-12")

    def test_batch_api_skips_empty_keyword_and_retains_partial_on_block(self):
        client = BaiduIndexClient("BDUSS=FIXTURE")
        def query(keyword, start, end, area):
            if keyword == "空": raise NoDataError("无数据")
            if keyword == "拦截": raise RequestBlockedError("request block")
            return [point(keyword, start, resolve_region(area))]
        client.query_keyword = query
        result = client.query_keywords(["空", "正常"], "2026-10-10", "2026-10-12")
        self.assertEqual([row.keyword for row in result], ["正常"])
        with self.assertRaises(RequestBlockedError) as exc:
            client.query_keywords(["正常", "拦截"], "2026-10-10", "2026-10-12")
        self.assertEqual([row.keyword for row in exc.exception.partial_rows], ["正常"])

    def test_command_line_exports_partial_results_before_reporting_block(self):
        class Client:
            def query_keywords(self, *args):
                exc = RequestBlockedError("request block")
                exc.partial_rows = [point("正常", "2026-10-10", resolve_region(0))]
                raise exc
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "partial.csv"
            stderr = io.StringIO()
            with patch("baidu_index_tool.service.BaiduIndexClient", return_value=Client()), patch.dict("os.environ", {"BAIDU_INDEX_COOKIE": "BDUSS=FIXTURE"}), contextlib.redirect_stderr(stderr):
                status = main(["query", "--keyword", "正常", "--start", "2026-10-10", "--end", "2026-10-12", "--output", str(target)])
            self.assertEqual(status, 5)
            self.assertTrue(target.exists())
            with target.open(encoding="utf-8-sig") as stream: self.assertEqual(len(list(csv.DictReader(stream))), 1)
            self.assertIn(str(target.resolve()), stderr.getvalue())
            self.assertNotIn("BDUSS", stderr.getvalue())

    def test_cancel_while_paused_stops_wait_without_request(self):
        api = api_for(None)
        api.toggle_pause()
        entered = threading.Event()
        errors = []
        def wait():
            entered.set()
            try: api._wait(1, "测试")
            except Exception as exc: errors.append(exc)
        # Use the actual wait implementation, not the fixture no-op.
        api._wait = type(api)._wait.__get__(api)
        thread = threading.Thread(target=wait)
        thread.start()
        self.assertTrue(entered.wait(1))
        api.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InterruptedError)

    def test_export_failure_keeps_checkpoint_and_resume_only_retries_export(self):
        calls = []
        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append(keyword)
                return [point(keyword, start, region)]
        with tempfile.TemporaryDirectory() as directory:
            api = api_for(Client())
            settings = config(directory)
            with patch.object(api, "_export", side_effect=OSError("disk fixture")):
                first = api.query(settings)
            self.assertTrue(first["incomplete"])
            self.assertTrue(first["canResume"])
            saved = json.loads(pathlib.Path(first["checkpoint"]).read_text(encoding="utf-8"))
            self.assertFalse(saved["complete"])
            self.assertEqual((saved["completed"], len(saved["batches"])), (1, 1))
            second = api.query(dict(settings, resume=True))
            self.assertFalse(second["incomplete"])
            self.assertTrue(pathlib.Path(second["outputs"][0]).exists())
            self.assertEqual(calls, ["供应链"])

    def test_failed_csv_write_preserves_previous_file_for_all_modes(self):
        writers = [write_csv, write_news_csv, write_brand_csv]
        with tempfile.TemporaryDirectory() as directory:
            for writer in writers:
                with self.subTest(writer=writer.__name__):
                    path = pathlib.Path(directory) / (writer.__name__ + ".csv")
                    path.write_bytes(b"PREVIOUS")
                    def broken():
                        raise OSError("iterator fixture")
                        yield
                    with self.assertRaises(OSError):
                        writer(broken(), path)
                    self.assertEqual(path.read_bytes(), b"PREVIOUS")
                    self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_shutdown_cancels_query_and_unpauses(self):
        api = api_for(None)
        api.toggle_pause()
        api.shutdown()
        self.assertTrue(api._cancel_event.is_set())
        self.assertFalse(api._pause_event.is_set())

    def test_window_created_after_shutdown_is_destroyed_not_registered(self):
        api = api_for(None)
        destroyed = []
        class Window:
            def destroy(self):
                destroyed.append(True)
                api.release_login_window(self)
        def factory():
            api.shutdown()
            return Window()
        api.set_login_window_factory(factory)
        self.assertIsNone(api._ensure_login_window())
        self.assertIsNone(api._current_login_window())
        self.assertEqual(destroyed, [True])


if __name__ == "__main__":
    unittest.main()
