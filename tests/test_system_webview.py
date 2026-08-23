from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import types
import unittest
from http.cookies import SimpleCookie

from baidu_index_tool.reverse_contract import BRAND_PATHS
from baidu_index_tool.models import BrandEntity, BrandIndexPoint
from baidu_index_tool.regions import resolve_region
from baidu_index_tool.webview_gui import SystemWebViewApi, _merge_cookie_headers, _row_dict


class _EventWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)
        return None


class _DialogWindow(_EventWindow):
    def __init__(self, selected):
        super().__init__()
        self.selected = selected
        self.dialog_calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.dialog_calls.append((dialog_type, kwargs))
        return self.selected


class _CookieWindow:
    def __init__(self, cookies):
        self.cookies = cookies
        self.hidden = False

    def get_cookies(self):
        return self.cookies

    def hide(self):
        self.hidden = True


class _WindowEvent:
    def __init__(self, value=False):
        self.value = value

    def is_set(self):
        return self.value

    def wait(self, _timeout=None):
        return self.value

    def set(self):
        self.value = True


class _LifecycleWindow(_CookieWindow):
    def __init__(self, cookies=(), closed=False):
        super().__init__(list(cookies))
        self.events = types.SimpleNamespace(
            closed=_WindowEvent(closed), loaded=_WindowEvent(True),
        )
        self.shown = False
        self.loaded_urls = []
        self.destroyed = False

    def show(self):
        if self.events.closed.is_set():
            raise RuntimeError("window already closed")
        self.shown = True

    def load_url(self, url):
        if self.events.closed.is_set():
            raise RuntimeError("window already closed")
        self.loaded_urls.append(url)

    def destroy(self):
        self.destroyed = True
        self.events.closed.set()


class _MainWindow(_EventWindow):
    def __init__(self):
        super().__init__()
        self.shown = False

    def show(self):
        self.shown = True


class _SignatureWindow:
    def __init__(self):
        self.calls = 0

    def evaluate_js(self, _script):
        self.calls += 1
        return json.dumps({
            "state": "READY",
            "value": "signed-value",
            "hasScript": True,
            "userAgent": "SystemWebView-Test",
        })


class _BrandClient:
    def __init__(self):
        self.resolve_calls = []
        self.metric_calls = []

    def resolve_brand(self, keyword):
        self.resolve_calls.append(keyword)
        return BrandEntity(keyword, "entity-1", "测试行业", "品牌")

    def query_brand_metric(self, entity, metric, start_date, _end_date, region):
        self.metric_calls.append((metric, region.area_id))
        values = {
            "brand_index": (100, None, None, None),
            "brand_search": (None, 20, None, None),
            "brand_news": (None, None, 70, None),
            "brand_interaction": (None, None, None, 10),
        }[metric]
        province, city = region.csv_location
        return [BrandIndexPoint(entity.name, start_date, province, city, *values)]


class _BrandApi(SystemWebViewApi):
    def __init__(self, client):
        super().__init__()
        self.client = client

    def _client(self, _cookie, _interval):
        return self.client

    def _wait(self, _seconds, _label):
        return None


class SystemWebViewTests(unittest.TestCase):
    def test_real_request_countdown_emits_4_3_2_1(self):
        window = _EventWindow()
        api = SystemWebViewApi()
        api.bind_windows(window, None, None)
        started = time.monotonic()
        result = api.preview_countdown(4)
        elapsed = time.monotonic() - started
        payload = "\n".join(window.scripts)
        for second in (4, 3, 2, 1):
            self.assertIn('"seconds": %d' % second, payload)
        self.assertIn('"seconds": 0', payload)
        self.assertGreaterEqual(elapsed, 3.9)
        self.assertGreaterEqual(result["elapsed"], 3.9)

    def test_output_directory_uses_native_folder_dialog(self):
        destination = pathlib.Path.home() / "Downloads" / "指数导出测试"
        window = _DialogWindow((str(destination),))
        api = SystemWebViewApi()
        api.bind_windows(window, None, None)
        fake_webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER=20))
        original = sys.modules.get("webview")
        sys.modules["webview"] = fake_webview
        try:
            result = api.choose_output_directory(str(pathlib.Path.home() / "Downloads"))
        finally:
            if original is None:
                sys.modules.pop("webview", None)
            else:
                sys.modules["webview"] = original
        self.assertTrue(result["selected"])
        self.assertEqual(result["path"], str(destination))
        self.assertEqual(window.dialog_calls[0][0], 20)

    def test_native_simplecookie_is_saved_with_login_and_device_values(self):
        login = SimpleCookie()
        login["BDUSS_BFESS"] = "login-token"
        login["BDUSS_BFESS"]["domain"] = ".baidu.com"
        login["BDUSS_BFESS"]["path"] = "/"
        device = SimpleCookie()
        device["BAIDUID"] = "device-token"
        device["BAIDUID"]["domain"] = ".baidu.com"
        device["BAIDUID"]["path"] = "/"
        header = SystemWebViewApi._cookie_header([login, device])
        self.assertIn("BDUSS_BFESS=login-token", header)
        self.assertIn("BAIDUID=device-token", header)

    def test_root_baidu_cookie_wins_over_passport_cookie(self):
        passport = SimpleCookie()
        passport["BDUSS_BFESS"] = "passport-token"
        passport["BDUSS_BFESS"]["domain"] = "passport.baidu.com"
        passport["BDUSS_BFESS"]["path"] = "/"
        root = SimpleCookie()
        root["BDUSS_BFESS"] = "root-token"
        root["BDUSS_BFESS"]["domain"] = ".baidu.com"
        root["BDUSS_BFESS"]["path"] = "/"

        header = SystemWebViewApi._cookie_header([passport, root])

        self.assertIn("BDUSS_BFESS=root-token", header)
        self.assertNotIn("passport-token", header)

    def test_open_login_recreates_a_closed_window(self):
        stale = _LifecycleWindow(closed=True)
        replacement = _LifecycleWindow()
        api = SystemWebViewApi()
        api.bind_windows(_MainWindow(), stale, stale)
        api.set_login_window_factory(lambda: replacement)

        result = api.open_login()

        self.assertTrue(result["opened"])
        self.assertIs(api._login_window, replacement)
        self.assertTrue(replacement.shown)
        self.assertEqual(replacement.loaded_urls, ["https://index.baidu.com/v2/index.html#/"])

    def test_cookie_sync_recovers_after_login_window_was_closed(self):
        login_cookie = SimpleCookie()
        login_cookie["BDUSS_BFESS"] = "persisted-token"
        login_cookie["BDUSS_BFESS"]["domain"] = ".baidu.com"
        login_cookie["BDUSS_BFESS"]["path"] = "/"
        stale = _LifecycleWindow(closed=True)
        replacement = _LifecycleWindow([login_cookie])
        main_window = _MainWindow()
        saved = {}
        api = SystemWebViewApi(sleep_fn=lambda _seconds: None)
        api.bind_windows(main_window, stale, stale)
        api.set_login_window_factory(lambda: replacement)
        api._load_account_document = lambda: {"cookies": [], "strategy": "round_robin"}
        api._save_account_document = lambda values, strategy: saved.update(values=list(values), strategy=strategy)

        result = api.sync_login_cookies()

        self.assertTrue(result["detected"])
        self.assertIn("BDUSS_BFESS=persisted-token", saved["values"][0])
        self.assertTrue(replacement.hidden)
        self.assertTrue(main_window.shown)

    def test_manual_cookie_sync_hides_login_and_returns_to_main_window(self):
        login_cookie = SimpleCookie()
        login_cookie["BDUSS_BFESS"] = "login-token"
        login_cookie["BDUSS_BFESS"]["domain"] = ".baidu.com"
        login_cookie["BDUSS_BFESS"]["path"] = "/"
        login_window = _CookieWindow([login_cookie])
        main_window = _MainWindow()
        saved = {}
        api = SystemWebViewApi()
        api.bind_windows(main_window, login_window, login_window)
        api._load_account_document = lambda: {"cookies": [], "strategy": "round_robin"}
        api._save_account_document = lambda values, strategy: saved.update(values=list(values), strategy=strategy)

        result = api.sync_login_cookies()

        self.assertTrue(result["detected"])
        self.assertTrue(login_window.hidden)
        self.assertTrue(main_window.shown)
        self.assertEqual(saved["strategy"], "round_robin")
        self.assertIn("BDUSS_BFESS=login-token", saved["values"][0])

    def test_device_cookie_merge_preserves_each_account_token(self):
        merged = _merge_cookie_headers(
            "BDUSS_BFESS=account-token; BAIDUID=old-device",
            "BAIDUID=current-device; HMACCOUNT=device-count",
        )
        self.assertIn("BDUSS_BFESS=account-token", merged)
        self.assertIn("BAIDUID=old-device", merged)
        self.assertIn("HMACCOUNT=device-count", merged)

    def test_signature_uses_preloaded_query_window(self):
        api = SystemWebViewApi()
        signature = _SignatureWindow()
        api._signature_window = signature
        value, user_agent = api._signature(timeout=0.1)
        self.assertEqual(value, "signed-value")
        self.assertEqual(user_agent, "SystemWebView-Test")
        self.assertEqual(signature.calls, 1)

    def test_brand_contract_has_four_distinct_endpoints(self):
        self.assertEqual(set(BRAND_PATHS), {
            "brand_index", "brand_search", "brand_news", "brand_interaction",
        })
        self.assertEqual(len(set(BRAND_PATHS.values())), 4)
        self.assertTrue(BRAND_PATHS["brand_index"].endswith("queryBrandIndex"))
        self.assertTrue(BRAND_PATHS["brand_search"].endswith("queryBrandSearchIndex"))
        self.assertTrue(BRAND_PATHS["brand_news"].endswith("queryBrandFeedIndex"))
        self.assertTrue(BRAND_PATHS["brand_interaction"].endswith("queryBrandClickIndex"))

    def test_brand_entity_is_resolved_once_and_metrics_stream_once_each(self):
        client = _BrandClient()
        api = _BrandApi(client)
        window = _EventWindow()
        api.bind_windows(window, None, None)
        metrics = ["brand_index", "brand_search", "brand_news", "brand_interaction"]
        rows = api._query_brand({
            "keywords": ["测试品牌"], "interval": 0, "strategy": "round_robin",
            "brandChannels": metrics, "startDate": "2026-08-07", "endDate": "2026-08-07",
        }, ["BDUSS=fixture"], [resolve_region(0), resolve_region(911)])
        self.assertEqual(client.resolve_calls, ["测试品牌"])
        self.assertEqual(len(client.metric_calls), 8)
        self.assertEqual(len(set(client.metric_calls)), 8)
        self.assertEqual(len(rows), 2)
        emitted = "\n".join(window.scripts)
        self.assertIn("品牌查询 1/9", emitted)
        self.assertIn('"done": 9', emitted)
        self.assertEqual(emitted.count("window.__pythonEvent && window.__pythonEvent(\"snapshot\""), 8)

    def test_missing_metric_is_preserved_for_dash_display(self):
        row = BrandIndexPoint("测试品牌", "2026-08-07", "全国", "全国", 100, None, 70, 10)
        payload = _row_dict(row)
        self.assertIsNone(payload["brand_search"])
        self.assertEqual(payload["brand_index"], 100)

    def test_pause_toggle_and_cancel_resume_wait_state(self):
        api = SystemWebViewApi()
        self.assertTrue(api.toggle_pause()["paused"])
        self.assertFalse(api.toggle_pause()["paused"])
        api.toggle_pause()
        api.cancel()
        self.assertFalse(api._pause_event.is_set())

    def test_interface_has_folder_button_and_no_removed_phrase(self):
        ui = pathlib.Path(__file__).parents[1] / "src" / "baidu_index_tool" / "webview_ui"
        content = "\n".join(path.read_text(encoding="utf-8") for path in ui.iterdir() if path.is_file())
        self.assertIn('id="chooseOutput"', content)
        self.assertIn('id="brandHelp"', content)
        self.assertIn('assets/econ-index-icon.png', content)
        self.assertIn('id="activityLog"', content)
        self.assertIn('id="countdownBadge"', content)
        self.assertIn("missing?'—'", content)
        self.assertNotIn("不" + "携带", content)

    def test_interface_copy_navigation_and_font_rules(self):
        root = pathlib.Path(__file__).parents[1]
        ui = root / "src" / "baidu_index_tool" / "webview_ui"
        html = (ui / "index.html").read_text(encoding="utf-8")
        css = "\n".join(path.read_text(encoding="utf-8") for path in ui.glob("*.css"))
        visible_copy = html + "\n" + (ui / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("经济与品牌指数数据工作台", visible_copy)
        self.assertNotIn("设计预览", visible_copy)
        self.assertNotIn("经" + "数", visible_copy)
        self.assertNotIn("系统 WebView", visible_copy)
        self.assertNotIn('id="engine"', html)
        self.assertNotIn('data-page="settings"', html)
        self.assertEqual(html.count('class="navItem'), 3)
        self.assertNotIn('data-page="community"', html)
        self.assertIn('id="aboutButton"', html)
        self.assertIn(".navItem b", css)
        self.assertRegex(css, r"\.navItem b\s*\{[^}]*white-space:\s*nowrap")
        self.assertRegex(css, r"\.navItem b\s*\{[^}]*word-break:\s*keep-all")
        self.assertIn("grid-template-columns: 88px 300px minmax(490px, 1fr) 0", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 20px minmax(0, 1fr)", css)
        self.assertRegex(css, r"\.dateRange input\s*\{[^}]*min-width:\s*0")
        self.assertRegex(css, r"\.workspace\s*\{[^}]*min-height:\s*0")
        self.assertIn(".statusTop > div:first-child", css)
        self.assertIn(".resultsHead > div:first-child", css)
        sizes = [int(value) for value in re.findall(r"font-size:\s*(\d+)px", css)]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 12)
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("避免堆放描述性、宣传性语言", agents)
        self.assertIn("用户可见文字不得小于 12px", agents)
        self.assertIn("中文导航名称始终单行显示", agents)

    def test_v240_region_about_and_repository_copy(self):
        root = pathlib.Path(__file__).parents[1]
        ui = root / "src" / "baidu_index_tool" / "webview_ui"
        html = (ui / "index.html").read_text(encoding="utf-8")
        script = (ui / "app.js").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        package_script = (root / "package_python_releases.sh").read_text(encoding="utf-8")

        result_meta = re.search(r'<p id="resultMeta">(.*?)</p>', html)
        self.assertIsNotNone(result_meta)
        self.assertNotIn("已选", result_meta.group(1))
        self.assertNotIn('id="selectedRegions"', result_meta.group(1))
        self.assertNotIn('class="selectedRegionBadge"', html)
        self.assertIn('class="selectedRegionsRow"', html)
        self.assertIn('id="selectedRegions"', html)
        self.assertLess(html.index('class="statusTop"'), html.index('class="selectedRegionsRow"'))
        self.assertLess(html.index('class="selectedRegionsRow"'), html.index('class="progress"'))

        self.assertIn('<button id="selectProvinces">选择全部省份</button>', html)
        self.assertIn('<button id="selectCities">选择全部城市</button>', html)
        self.assertIn('<button id="clearRegions">清空</button>', html)
        self.assertIn("$('selectCities').onclick", script)

        self.assertNotIn('data-page="community"', html)
        self.assertNotIn('>沟通交流<', html)
        self.assertIn('id="aboutButton"', html)
        self.assertIn('class="aboutIcon"', html)
        self.assertIn('版本：v2.4.0', html)
        self.assertIn('QQ群：610645081', html)
        self.assertIn("$('aboutButton').onclick=openAbout", script)
        self.assertNotIn("if(page==='community')", script)
        self.assertNotRegex(script, r"setInterval\([^\n]*syncCookies")
        self.assertIn("button.disabled=true", script)

        self.assertIn('欢迎关注微信公众号“经实研读”，或加入QQ群：610645081', readme)
        self.assertTrue((root / ".gitignore").is_file())
        self.assertIn("GITHUB_ZIP=", package_script)
        self.assertIn("github_root", package_script)
        self.assertIn("macOS-x64.zip", package_script)
        self.assertIn("macOS-arm64.zip", package_script)
        self.assertNotIn("内嵌浏览器", readme + package_script + html)
        self.assertFalse((root / "run_embedded_webview_gui.py").exists())
        self.assertFalse((root / "requirements-gui.txt").exists())
        mac_workflow = (root / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")
        self.assertIn("macos-15-intel", mac_workflow)
        self.assertIn("runner: macos-15", mac_workflow)

    def test_bootstrap_has_no_engine_label(self):
        value = SystemWebViewApi().bootstrap()
        self.assertNotIn("engine", value)


if __name__ == "__main__":
    unittest.main()
