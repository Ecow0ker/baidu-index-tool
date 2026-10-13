from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from baidu_index_tool.api import ApiError, BaiduIndexClient, RequestBlockedError, ResponseFormatError, parse_search_payload
from baidu_index_tool.crypto import build_translation_table
from baidu_index_tool.models import BrandEntity, BrandIndexPoint, IndexPoint
from baidu_index_tool.regions import all_regions, resolve_region
from baidu_index_tool.webview_gui import SystemWebViewApi


class QueryRecoveryTests(unittest.TestCase):
    def test_block_is_distinct_from_generic_10001(self):
        for code in (10001, "10001"):
            with self.assertRaises(RequestBlockedError):
                BaiduIndexClient._check_status({"status": code, "message": " Request  Block "})
        with self.assertRaises(ApiError) as result:
            BaiduIndexClient._check_status({"status": 10001, "message": "请求失败，请重试！"})
        self.assertNotIsInstance(result.exception, RequestBlockedError)

    def test_ptbk_block_is_propagated_without_endpoint_fallback(self):
        calls = []
        search = json.loads((pathlib.Path(__file__).parent / "fixtures/search_response.json").read_text(encoding="utf-8"))

        class Transport:
            def get_json(self, url, params, headers):
                calls.append(url)
                return {"status": 10001, "message": "request block"} if "ptbk" in url else search

        client = BaiduIndexClient("BDUSS=FIXTURE", transport=Transport(), retries=2)
        with self.assertRaises(RequestBlockedError):
            client.query_keyword("测试", "2025-01-01", "2025-01-03")
        self.assertEqual(len(calls), 2)

    def test_screenshot_29_of_362_is_exported_and_resumes_without_duplicates(self):
        failed_region = resolve_region("河南 / 平顶山")
        others = [item for item in all_regions() if item.area_id != failed_region.area_id]
        regions = others[:29] + [failed_region] + others[29:361]
        calls = []
        blocked = [True]

        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append(region.area_id)
                if blocked[0] and region.area_id == failed_region.area_id:
                    BaiduIndexClient._check_status({"status": 10001, "message": "request block"})
                province, city = region.csv_location
                return [IndexPoint(keyword, start, province, city, 10, 10, 0)]

        with tempfile.TemporaryDirectory() as directory:
            api = SystemWebViewApi()
            api._load_account_document = lambda: {
                "cookies": ["BDUSS=FIXTURE_%d" % i for i in range(116)],
                "strategy": "round_robin",
            }
            api._client = lambda *_args: Client()
            api._wait = lambda *_args: None
            config = {
                "keywords": ["供应链"], "regionIds": [r.area_id for r in regions],
                "startDate": "2011-01-01", "endDate": "2026-10-10",
                "indexMode": "search", "searchChannels": ["overall", "pc", "mobile"],
                "period": "yearly", "interval": 4, "outputDir": directory,
            }
            result = api.query(config)
            self.assertTrue(result["blocked"])
            self.assertTrue(result["canResume"])
            self.assertEqual((result["completed"], result["total"]), (29, 362))
            self.assertEqual(len(result["rows"]), 29)
            self.assertEqual(len(calls), 30)
            with pathlib.Path(result["outputs"][0]).open(encoding="utf-8-sig") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 29)
            progress = json.loads(pathlib.Path(result["checkpoint"]).read_text(encoding="utf-8"))
            self.assertEqual(len(progress["completedTasks"]), 29)
            self.assertNotIn("BDUSS", json.dumps(progress))

            blocked[0] = False
            resumed = api.query(dict(config, resume=True, interval=10))
            self.assertFalse(resumed["incomplete"])
            self.assertEqual((resumed["completed"], resumed["total"]), (362, 362))
            self.assertEqual(len(resumed["rows"]), 362)
            self.assertEqual(len(calls), 363)
            self.assertEqual(calls.count(failed_region.area_id), 2)
            self.assertEqual(resumed["runDir"], result["runDir"])
            with pathlib.Path(resumed["outputs"][0]).open(encoding="utf-8-sig") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 362)

    def test_every_http_request_including_ptbk_has_the_configured_gap(self):
        here = pathlib.Path(__file__).parent / "fixtures"
        search = json.loads((here / "search_response.json").read_text(encoding="utf-8"))
        ptbk = json.loads((here / "ptbk_response.json").read_text(encoding="utf-8"))
        clock = [100.0]
        sent = []

        class Transport:
            def get_json(self, url, params, headers):
                sent.append(clock[0])
                return ptbk if "ptbk" in url else search

        api = SystemWebViewApi()
        api._signature = lambda: ("FIXTURE", "FixtureWebView")
        api._wait = lambda seconds, label: clock.__setitem__(0, clock[0] + seconds)
        with patch("baidu_index_tool.webview_gui.time.monotonic", side_effect=lambda: clock[0]):
            client = api._client("BDUSS=FIXTURE", 4)
            client.transport = Transport()
            client.query_keyword("测试", "2025-01-01", "2025-01-03")
            client.query_keyword("测试", "2025-01-01", "2025-01-03")
        self.assertEqual(sent, [100.0, 104.0, 108.0, 112.0])

    def test_zero_interval_still_checks_stop(self):
        api = SystemWebViewApi()
        api.cancel()
        with self.assertRaises(InterruptedError):
            api._wait(0, "测试")

    def test_long_query_uses_daily_windows_including_leap_day(self):
        transport = self._daily_transport()
        client = BaiduIndexClient("BDUSS=FIXTURE", transport=transport)
        rows = client.query_keyword("测试", "2024-01-01", "2024-12-31")
        self.assertEqual(len(rows), 366)
        self.assertEqual(len({r.date for r in rows}), 366)
        self.assertEqual((rows[0].date, rows[-1].date), ("2024-01-01", "2024-12-31"))
        self.assertEqual(transport.windows, [("2024-01-01", "2024-12-30"), ("2024-12-31", "2024-12-31")])

    def test_long_query_block_retains_completed_time_windows(self):
        transport = self._daily_transport(block_on=2)
        client = BaiduIndexClient("BDUSS=FIXTURE", transport=transport)
        with self.assertRaises(RequestBlockedError) as result:
            client.query_keyword("测试", "2024-01-01", "2024-12-31")
        self.assertEqual(len(result.exception.partial_rows), 365)
        self.assertEqual(len(transport.windows), 2)

    def test_weekly_response_is_not_mislabeled_as_daily(self):
        root = pathlib.Path(__file__).parent / "fixtures"
        response = json.loads((root / "search_response.json").read_text(encoding="utf-8"))
        response["data"]["userIndexes"][0]["type"] = "week"
        key = json.loads((root / "ptbk_response.json").read_text(encoding="utf-8"))["data"]
        with self.assertRaises(ResponseFormatError):
            parse_search_payload(response, key, "测试", resolve_region("全国"))

    def test_news_block_preserves_search_rows_and_replaces_them_on_resume(self):
        blocked = [True]

        class Client:
            def query_keyword(self, keyword, start, end, region):
                return [IndexPoint(keyword, start, "全国", "全国", 10, 10, 0)]

            def query_news_keyword(self, keyword, start, end, region):
                if blocked[0]:
                    raise RequestBlockedError("request block")
                return [IndexPoint(keyword, start, "全国", "全国", None, None, None, 8)]

        with tempfile.TemporaryDirectory() as directory:
            api = self._fixture_api(Client())
            config = self._config(directory, searchChannels=["overall", "news"])
            first = api.query(config)
            self.assertTrue(first["blocked"])
            self.assertEqual(first["completed"], 0)
            self.assertEqual(first["rows"][0]["overall"], 10)
            self.assertIsNone(first["rows"][0]["news"])
            blocked[0] = False
            second = api.query(dict(config, resume=True))
            self.assertEqual(len(second["rows"]), 1)
            self.assertEqual(second["rows"][0]["news"], 8)

    def test_brand_block_preserves_completed_metric(self):
        class Client:
            def resolve_brand(self, keyword):
                return BrandEntity(keyword, "FIXTURE", "测试", "品牌")

            def query_brand_metric(self, entity, metric, start, end, region):
                if metric == "brand_news":
                    raise RequestBlockedError("request block")
                return [BrandIndexPoint(entity.name, start, "全国", "全国", None, 20, None, None)]

        with tempfile.TemporaryDirectory() as directory:
            api = self._fixture_api(Client())
            result = api.query(self._config(directory, indexMode="brand", brandChannels=["brand_search", "brand_news"]))
            self.assertTrue(result["blocked"])
            self.assertEqual(result["rows"][0]["brand_search"], 20)
            self.assertEqual(len(result["outputs"]), 1)

    def test_large_result_limits_preview_but_exports_every_row(self):
        with tempfile.TemporaryDirectory() as directory:
            api = SystemWebViewApi()
            config = self._config(directory)
            start = dt.date(2010, 1, 1)
            api._task_rows[(0, "测试")] = [IndexPoint("测试", (start + dt.timedelta(days=i)).isoformat(), "全国", "全国", 10, 10, 0)
                                          for i in range(2500)]
            api._completed_tasks.add((0, "测试"))
            result = api._query_result(config)
            self.assertEqual(result["rowCount"], 2500)
            self.assertEqual(len(result["rows"]), 2000)
            with pathlib.Path(result["outputs"][0]).open(encoding="utf-8-sig") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2500)

    def test_stop_retains_completed_rows_and_changed_task_starts_fresh(self):
        api = SystemWebViewApi()
        stopped = [False]

        class Client:
            def query_keyword(self, keyword, start, end, region):
                if region.area_id == 911 and not stopped[0]:
                    stopped[0] = True
                    api.cancel()
                    raise InterruptedError("任务已停止")
                province, city = region.csv_location
                return [IndexPoint(keyword, start, province, city, 10, 10, 0)]

        with tempfile.TemporaryDirectory() as directory:
            api = self._fixture_api(Client(), api)
            config = self._config(directory, regionIds=[0, 911])
            first = api.query(config)
            self.assertTrue(first["cancelled"])
            self.assertEqual(len(first["rows"]), 1)
            self.assertEqual(len(first["outputs"]), 1)
            second = api.query(dict(config, resume=True, keywords=["新关键词"]))
            self.assertFalse(second["incomplete"])
            self.assertEqual({r["keyword"] for r in second["rows"]}, {"新关键词"})

    @staticmethod
    def _fixture_api(client, api=None):
        api = api or SystemWebViewApi()
        api._load_account_document = lambda: {"cookies": ["BDUSS=FIXTURE"], "strategy": "round_robin"}
        api._client = lambda *_args: client
        api._wait = lambda *_args: None
        return api

    @staticmethod
    def _config(directory, **values):
        config = {"keywords": ["测试"], "regionIds": [0], "startDate": "2025-01-01", "endDate": "2025-01-02",
                  "period": "daily", "interval": 0, "outputDir": directory, "indexMode": "search", "searchChannels": ["overall"]}
        config.update(values)
        return config

    @staticmethod
    def _daily_transport(block_on=0):
        key = json.loads((pathlib.Path(__file__).parent / "fixtures/ptbk_response.json").read_text(encoding="utf-8"))["data"]
        inverse = {value: name for name, value in build_translation_table(key).items()}

        class Transport:
            def __init__(self):
                self.windows = []

            def get_json(self, url, params, headers):
                if "ptbk" in url:
                    return {"status": 0, "data": key}
                first, last = params["startDate"], params["endDate"]
                self.windows.append((first, last))
                if len(self.windows) == block_on:
                    return {"status": 10001, "message": "request block"}
                count = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1
                cipher = "".join(inverse[c] for c in ",".join(["10"] * count))
                channel = {"startDate": first, "endDate": last, "data": cipher}
                return {"status": 0, "data": {"uniqid": "FIXTURE", "userIndexes": [
                    {"type": "day", "all": channel, "pc": channel, "wise": channel},
                ]}}

        return Transport()


if __name__ == "__main__":
    unittest.main()
