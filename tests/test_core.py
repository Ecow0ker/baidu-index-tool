from __future__ import annotations

import csv
import json
import pathlib
import tempfile
import unittest

from baidu_index_tool.aggregate import aggregate_brand_points, aggregate_points
from baidu_index_tool.api import (
    ApiError, AuthenticationError, BadRequestError, BaiduIndexClient, RateLimitError,
    parse_news_payload, parse_search_payload,
)
from baidu_index_tool.crypto import DecryptionError, build_translation_table, decrypt_series
from baidu_index_tool.export import HEADERS, read_csv, write_brand_csv, write_csv
from baidu_index_tool.models import BrandIndexPoint, IndexPoint
from baidu_index_tool.regions import find_regions, resolve_region
from baidu_index_tool.service import load_cookie
from baidu_index_tool.task_logic import output_filename, validate_task
from baidu_index_tool.webview_gui import SystemWebViewApi, _normalise_accounts

HERE = pathlib.Path(__file__).resolve().parent


def fixture(name):
    return json.loads((HERE / "fixtures" / name).read_text(encoding="utf-8"))


class FakeTransport:
    def __init__(self, search=None, ptbk=None):
        self.search = search or fixture("search_response.json")
        self.ptbk = ptbk or fixture("ptbk_response.json")
        self.calls = []

    def get_json(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        if "SearchApi/index" in url:
            return self.search
        if "FeedSearchApi" in url:
            return self.search
        if "ptbk" in url:
            return self.ptbk
        raise AssertionError(url)


class SequencedNewsTransport(FakeTransport):
    def __init__(self):
        super().__init__()
        self.news_responses = [
            {"status": 10001, "message": "exceed speed limit"},
            fixture("search_response.json"),
        ]

    def get_json(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        if "FeedSearchApi" in url:
            return self.news_responses.pop(0)
        if "ptbk" in url:
            return self.ptbk
        raise AssertionError(url)


class BrandTransport(FakeTransport):
    def post_json(self, url, body, headers):
        self.calls.append((url, dict(body), dict(headers)))
        if "word/sug" in url:
            return {"status": 0, "data": {"result": [{
                "word": "华为", "type": "brand",
                "brandInfo": {"id": "100024203", "type": "brand", "trade2Name": "手机行业"},
            }]}}
        if "queryBrandSearchIndex" in url:
            return {"status": 0, "data": [
                {"statDate": "2025-01-01", "value": 100},
                {"statDate": "2025-01-02", "value": 120},
            ]}
        raise AssertionError(url)


class CryptoTests(unittest.TestCase):
    def test_translation_and_decryption(self):
        key = fixture("ptbk_response.json")["data"]
        self.assertEqual(build_translation_table(key)["k"], ",")
        self.assertEqual(decrypt_series("bakcakda", key), [10, 20, 30])

    def test_bad_ptbk(self):
        with self.assertRaises(DecryptionError):
            decrypt_series("abc", "odd")

    def test_empty_value_inside_series_is_zero(self):
        ptbk = fixture("ptbk_response.json")["data"]
        inverse = {value: key for key, value in build_translation_table(ptbk).items()}
        cipher = "".join(inverse[value] for value in "10,,30")
        self.assertEqual(decrypt_series(cipher, ptbk), [10, 0, 30])


class ApiTests(unittest.TestCase):
    def test_parse_fixture(self):
        rows = parse_search_payload(
            fixture("search_response.json"), fixture("ptbk_response.json")["data"],
            "fallback", resolve_region("北京"),
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], IndexPoint("测试", "2025-01-01", "北京", "北京", 10, 6, 4))
        self.assertEqual(rows[-1].overall, 30)

    def test_client_flow_and_headers(self):
        transport = FakeTransport()
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=0)
        rows = client.query_keyword("测试", "2025-01-01", "2025-01-03", "北京")
        self.assertEqual([x.overall for x in rows], [10, 20, 30])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0][1]["area"], "911")
        self.assertNotIn("BDUSS=fixture", str(transport.calls[0][1]))

    def test_dynamic_browser_signature_is_added_only_to_trend_request(self):
        transport = FakeTransport()
        signatures = []

        def signature_provider():
            signatures.append(True)
            return "fixture-cipher", "FixtureBrowser/140"

        client = BaiduIndexClient(
            "BDUSS=fixture", transport=transport, retries=0,
            signature_provider=signature_provider,
        )
        client.query_keyword("测试", "2025-01-01", "2025-01-03", "北京")
        self.assertEqual(len(signatures), 1)
        self.assertEqual(transport.calls[0][2]["Cipher-Text"], "fixture-cipher")
        self.assertEqual(transport.calls[0][2]["User-Agent"], "FixtureBrowser/140")
        self.assertNotIn("Cipher-Text", transport.calls[1][2])

    def test_authentication_error(self):
        transport = FakeTransport(search={"status": 10000, "message": "未登录"})
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=0)
        with self.assertRaises(AuthenticationError):
            client.query_keyword("测试", "2025-01-01", "2025-01-03")

    def test_bad_request_error_from_reversed_status_contract(self):
        transport = FakeTransport(search={"status": 10002, "message": "bad request"})
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=0)
        with self.assertRaises(BadRequestError):
            client.query_keyword("测试", "2025-01-01", "2025-01-03")

    def test_generic_10001_does_not_claim_account_is_limited(self):
        transport = FakeTransport(search={"status": 10001, "message": "请求失败，请重试！"})
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=2, retry_delay=0)
        with self.assertRaisesRegex(ApiError, "接口状态 10001") as result:
            client.query_keyword("测试", "2025-01-01", "2025-01-03")
        self.assertNotIsInstance(result.exception, RateLimitError)
        self.assertEqual(len(transport.calls), 1)

    def test_explicit_rate_limit_switches_immediately(self):
        transport = FakeTransport(search={"status": 10001, "message": "exceed speed limit"})
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=2, retry_delay=0)
        with self.assertRaises(RateLimitError):
            client.query_keyword("测试", "2025-01-01", "2025-01-03")
        self.assertEqual(len(transport.calls), 1)

    def test_news_uses_same_ptbk_decode(self):
        rows = parse_news_payload(
            fixture("search_response.json"), fixture("ptbk_response.json")["data"],
            "测试", resolve_region("北京"),
        )
        self.assertEqual([x.news for x in rows], [10, 20, 30])
        self.assertTrue(all(x.overall is None for x in rows))

    def test_news_current_index_envelope(self):
        rows = parse_news_payload(
            fixture("feed_response.json"), fixture("ptbk_response.json")["data"],
            "回退", resolve_region("全国"),
        )
        self.assertEqual([x.keyword for x in rows], ["资讯测试"] * 3)
        self.assertEqual([x.news for x in rows], [10, 20, 30])

    def test_news_rate_limit_is_not_retried_on_same_account(self):
        transport = SequencedNewsTransport()
        client = BaiduIndexClient(
            "BDUSS=fixture", transport=transport, retries=1, retry_delay=0,
        )
        with self.assertRaises(RateLimitError):
            client.query_news_keyword("测试", "2025-01-01", "2025-01-03", "北京")
        self.assertEqual(len(transport.calls), 1)

    def test_brand_resolution_and_metric_query(self):
        transport = BrandTransport()
        client = BaiduIndexClient("BDUSS=fixture", transport=transport, retries=0)
        entity = client.resolve_brand("华为")
        self.assertEqual((entity.name, entity.entity_id, entity.industry), ("华为", "100024203", "手机行业"))
        rows = client.query_brand_metric(entity, "brand_search", "2025-01-01", "2025-01-02", "北京")
        self.assertEqual([row.brand_search for row in rows], [100, 120])
        self.assertEqual(transport.calls[-1][1]["regionId"], 1)
        self.assertEqual(transport.calls[-1][1]["source"], 0)


class AggregateExportTests(unittest.TestCase):
    def setUp(self):
        self.rows = parse_search_payload(
            fixture("search_response.json"), fixture("ptbk_response.json")["data"],
            "测试", resolve_region("全国"),
        )

    def test_monthly_mean(self):
        rows = aggregate_points(self.rows, "monthly", "mean")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, "20250101-20250103")
        self.assertEqual((rows[0].overall, rows[0].pc, rows[0].mobile), (20, 12, 8))

    def test_sum(self):
        rows = aggregate_points(self.rows, "overall", "sum")
        self.assertEqual((rows[0].overall, rows[0].pc, rows[0].mobile), (60, 36, 24))

    def test_csv_roundtrip_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "out.csv"
            write_csv(self.rows, path)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), HEADERS)
            self.assertEqual(read_csv(path), self.rows)

    def test_brand_aggregate_and_export(self):
        points = [
            BrandIndexPoint("华为", "2025-01-01", "北京", "北京", 100, 20, 70, 10),
            BrandIndexPoint("华为", "2025-01-02", "北京", "北京", 120, 30, 75, 15),
        ]
        rows = aggregate_brand_points(points, "monthly", "mean")
        self.assertEqual((rows[0].brand_index, rows[0].brand_search), (110, 25))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_brand_csv(rows, pathlib.Path(tmp) / "brand.csv")
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                content = list(csv.reader(handle))
            self.assertEqual(content[0][4:], ["品牌指数", "品牌搜索指数", "品牌资讯指数", "品牌互动指数"])
            self.assertEqual(content[1][4:], ["110", "25", "73", "13"])


class RegionCookieTests(unittest.TestCase):
    def test_regions(self):
        self.assertEqual(resolve_region("全国").area_id, 0)
        self.assertEqual(resolve_region("北京").area_id, 911)
        self.assertEqual(resolve_region("广东 / 深圳").area_id, 94)
        self.assertTrue(any(x.name == "深圳" for x in find_regions("广东")))

    def test_netscape_cookie_file(self):
        text = "# Netscape HTTP Cookie File\n.example.test\tTRUE\t/\tTRUE\t0\tA\t1\n.example.test\tTRUE\t/\tTRUE\t0\tB\t2\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cookies.txt"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(load_cookie(path), "A=1; B=2")

    def test_multi_account_cookie_normalisation(self):
        values = _normalise_accounts(
            "BDUSS=one; A=1\ninvalid=1\nBDUSS_BFESS=two; B=2\nBDUSS=one; A=1\n"
        )
        self.assertEqual(values, ["BDUSS=one; A=1", "BDUSS_BFESS=two; B=2"])

    def test_worker_round_robin_account_order(self):
        self.assertEqual(SystemWebViewApi._account_order(3, 0, "round_robin"), [0, 1, 2])
        self.assertEqual(SystemWebViewApi._account_order(3, 1, "round_robin"), [1, 2, 0])
        self.assertEqual(SystemWebViewApi._account_order(3, 2, "round_robin"), [2, 0, 1])

    def test_worker_switches_account_after_rate_limit(self):
        class SwitchingClient:
            def __init__(self, cookie, **_kwargs):
                self.cookie = cookie

            def query_keyword(self, *_args):
                if self.cookie == "first":
                    raise RateLimitError("fixture")
                return []

        api = SystemWebViewApi()
        api._wait = lambda _seconds, _label: None
        api._client = lambda cookie, _interval: SwitchingClient(cookie)
        config = {
            "keywords": ["测试"], "startDate": "2025-01-01", "endDate": "2025-01-03",
            "interval": 0, "strategy": "round_robin", "searchChannels": ["overall"],
        }
        rows = api._query_search(config, ["first", "second"], [resolve_region("全国")])
        self.assertEqual(rows, [])

    def test_reversed_task_guards_and_filename(self):
        summary = validate_task(
            ["测试"], [resolve_region("北京")], "2025-01-01", "2025-01-03",
            has_cookie=True, has_channel=True,
        )
        self.assertEqual(summary.request_count, 1)
        self.assertEqual(
            output_filename("keyword", "2025年01月01日 00时00分00秒", "yearly", "2025-01-01", "2025-01-03", keyword="测试"),
            "测试-年（日均）-20250101-20250103.csv",
        )


if __name__ == "__main__":
    unittest.main()
