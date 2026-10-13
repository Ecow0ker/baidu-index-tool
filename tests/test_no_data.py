from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from baidu_index_tool.api import BaiduIndexClient, NoDataError, RequestBlockedError, ResponseFormatError, parse_search_payload
from baidu_index_tool.crypto import DecryptionError, build_translation_table
from baidu_index_tool.models import IndexPoint
from baidu_index_tool.regions import resolve_region
from baidu_index_tool.transport import NetworkError, TLSCertificateError
from baidu_index_tool.webview_gui import SystemWebViewApi


PROVINCES = [911, 910, 913, 923, 927, 914, 904, 916, 906, 917, 909, 921, 901, 924, 920, 907,
             922, 915, 926, 912, 929, 908, 903, 928, 905, 925, 930, 902, 919, 918, 932, 933, 934, 931]


def api_for(client):
    api = SystemWebViewApi()
    api._load_account_document = lambda: {"cookies": ["BDUSS=FIXTURE_A", "BDUSS=FIXTURE_B"], "strategy": "round_robin"}
    api._client = lambda *_args: client
    api._wait = lambda *_args: None
    return api


def config(directory, **values):
    result = {"keywords": ["供应链"], "regionIds": [911], "startDate": "2026-09-12", "endDate": "2026-10-12",
              "period": "daily", "interval": 4, "outputDir": directory, "indexMode": "search",
              "searchChannels": ["overall", "pc", "mobile"]}
    result.update(values)
    return result


def point(keyword, date, region):
    province, city = region.csv_location
    return IndexPoint(keyword, date, province, city, 10, 0, 10)


class NoDataTests(unittest.TestCase):
    def test_screenshot_899_rows_finishes_34_tasks_and_resume_skips_no_data(self):
        calls, events = [], []

        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append(region.area_id)
                if region.area_id in PROVINCES[29:]:
                    raise NoDataError("该条件没有指数数据")
                first = dt.date.fromisoformat(start)
                return [point(keyword, (first + dt.timedelta(days=i)).isoformat(), region) for i in range(31)]

        with tempfile.TemporaryDirectory() as directory:
            api = api_for(Client())
            api._emit = lambda name, data: events.append((name, data))
            settings = config(directory, regionIds=PROVINCES)
            result = api.query(settings)
            self.assertEqual((result["completed"], result["dataCount"], result["noDataCount"], result["rowCount"]), (34, 29, 5, 899))
            self.assertFalse(result["incomplete"])
            self.assertFalse(result["canResume"])
            self.assertEqual(calls, PROVINCES)
            with pathlib.Path(result["outputs"][0]).open(encoding="utf-8-sig") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 899)
            record = json.loads(pathlib.Path(result["checkpoint"]).read_text(encoding="utf-8"))
            self.assertTrue(record["complete"])
            self.assertEqual(len(record["noDataTasks"]), 5)
            self.assertEqual(len(record["noDataDetails"]), 5)
            self.assertNotIn("BDUSS", json.dumps(record))
            final_progress = [data for name, data in events if name == "progress"][-1]
            self.assertEqual((final_progress["done"], final_progress["dataCount"], final_progress["noDataCount"]), (34, 29, 5))
            api.query(dict(settings, resume=True))
            self.assertEqual(calls, PROVINCES)

    def test_no_data_partial_rows_are_retained(self):
        class Client:
            def query_keyword(self, keyword, start, end, region):
                exc = NoDataError("部分日期没有数据")
                exc.partial_rows = [point(keyword, start, region)]
                raise exc

        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory))
            self.assertFalse(result["incomplete"])
            self.assertEqual((result["dataCount"], result["noDataCount"], result["rowCount"]), (1, 0, 1))

    def test_no_data_before_block_is_not_retried_on_resume(self):
        blocked, calls = [True], []

        class Client:
            def query_keyword(self, keyword, start, end, region):
                calls.append(region.area_id)
                if region.area_id == 918:
                    raise NoDataError("该条件没有指数数据")
                if blocked[0]:
                    raise RequestBlockedError("request block")
                return [point(keyword, start, region)]

        with tempfile.TemporaryDirectory() as directory:
            api = api_for(Client())
            settings = config(directory, regionIds=[918, 911])
            first = api.query(settings)
            self.assertTrue(first["blocked"])
            self.assertEqual((first["completed"], first["noDataCount"]), (1, 1))
            blocked[0] = False
            second = api.query(dict(settings, resume=True))
            self.assertEqual((second["completed"], second["noDataCount"], second["rowCount"]), (2, 1, 1))
            self.assertEqual(calls, [918, 911, 911])
            fresh = api.query(dict(settings, keywords=["新关键词"]))
            self.assertEqual(fresh["noDataCount"], 1)
            self.assertEqual(len(json.loads(pathlib.Path(fresh["checkpoint"]).read_text(encoding="utf-8"))["noDataDetails"]), 1)

    def test_news_survives_empty_search_and_missing_search_values_are_not_zero(self):
        class Client:
            def query_keyword(self, *args):
                raise NoDataError("没有搜索数据")

            def query_news_keyword(self, keyword, start, end, region):
                return [IndexPoint(keyword, start, *region.csv_location, None, None, None, 7)]

        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory, searchChannels=["overall", "news"]))
            self.assertEqual((result["rowCount"], result["dataCount"], result["noDataCount"]), (1, 1, 0))
            self.assertEqual(result["rows"][0]["news"], 7)
            self.assertIsNone(result["rows"][0]["overall"])

    def test_news_partial_no_data_preserves_both_selected_metrics(self):
        class Client:
            def query_keyword(self, keyword, start, end, region):
                return [point(keyword, start, region)]

            def query_news_keyword(self, keyword, start, end, region):
                exc = NoDataError("部分资讯日期没有数据")
                exc.partial_rows = [IndexPoint(keyword, start, *region.csv_location, None, None, None, 7)]
                raise exc

        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory, searchChannels=["overall", "news"]))
            self.assertFalse(result["incomplete"])
            self.assertEqual((result["rows"][0]["overall"], result["rows"][0]["news"]), (10, 7))

    def test_all_missing_creates_record_but_no_fake_csv(self):
        class Client:
            def query_keyword(self, *args):
                raise NoDataError("该条件没有指数数据")

        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory))
            self.assertFalse(result["incomplete"])
            self.assertEqual((result["completed"], result["noDataCount"], result["rows"], result["outputs"]), (1, 1, [], []))
            self.assertTrue(pathlib.Path(result["checkpoint"]).is_file())

    def test_selected_missing_is_not_the_same_as_selected_zero(self):
        class Client:
            def query_keyword(self, keyword, start, end, region):
                return [IndexPoint(keyword, start, *region.csv_location, 10, None if region.area_id == 918 else 0, 10)]

        with tempfile.TemporaryDirectory() as directory:
            result = api_for(Client()).query(config(directory, regionIds=[918, 911], searchChannels=["pc"]))
            self.assertEqual((result["noDataCount"], result["dataCount"], result["rowCount"]), (1, 1, 1))
            self.assertEqual(result["rows"][0]["pc"], 0)

    def test_real_errors_are_still_fatal_and_preserve_results(self):
        for error_type in (ResponseFormatError, DecryptionError, NetworkError, TLSCertificateError):
            with self.subTest(error=error_type.__name__), tempfile.TemporaryDirectory() as directory:
                class Client:
                    def query_keyword(self, keyword, start, end, region):
                        if region.area_id == 918:
                            raise error_type("FIXTURE ERROR")
                        return [point(keyword, start, region)]

                result = api_for(Client()).query(config(directory, regionIds=[911, 918, 932]))
                self.assertTrue(result["incomplete"])
                self.assertTrue(result["canResume"])
                self.assertEqual((result["completed"], result["rowCount"], result["noDataCount"]), (1, 1, 0))

    def test_empty_middle_or_first_window_does_not_hide_later_dates(self):
        for metric in ("search", "news"):
            for missing in ({1}, {2}, {1, 2, 3}):
                with self.subTest(metric=metric, missing=missing):
                    transport = window_transport(missing)
                    notices = []
                    client = BaiduIndexClient("BDUSS=FIXTURE", transport=transport, on_no_data=lambda *args: notices.append(args))
                    query = client.query_keyword if metric == "search" else client.query_news_keyword
                    if len(missing) == 3:
                        with self.assertRaises(NoDataError):
                            query("测试", "2023-01-01", "2025-01-01")
                    else:
                        rows = query("测试", "2023-01-01", "2025-01-01")
                        self.assertEqual(len(rows), 367)
                        self.assertEqual(rows[-1].date, "2025-01-01")
                    self.assertEqual(len(transport.windows), 3)
                    self.assertEqual(len(notices), len(missing))

    def test_malformed_entries_are_not_reported_as_no_data(self):
        for entry in (None, "bad", {}, {"type": "week", "all": {"data": ""}}):
            with self.subTest(entry=entry), self.assertRaises(ResponseFormatError):
                parse_search_payload({"data": {"userIndexes": [entry]}}, "FIXTURE", "测试", resolve_region(0))


def window_transport(missing):
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
            count = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1
            cipher = "" if len(self.windows) in missing else "".join(inverse[c] for c in ",".join(["10"] * count))
            channel = {"startDate": first, "endDate": last, "data": cipher}
            return {"status": 0, "data": {"uniqid": "FIXTURE", "userIndexes": [{"all": channel, "pc": channel, "wise": channel}]}}

    return Transport()


if __name__ == "__main__":
    unittest.main()
