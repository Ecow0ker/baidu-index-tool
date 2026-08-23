from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import pathlib
import platform
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .aggregate import aggregate_brand_points, aggregate_points
from .api import AuthenticationError, BaiduIndexClient, NoDataError, RateLimitError
from .export import write_brand_csv, write_csv, write_news_csv
from .models import BrandIndexPoint, IndexPoint, Region
from .regions import all_regions, resolve_region
from .task_logic import create_run_directory, output_filename, validate_task

LOGIN_URL = "https://index.baidu.com/v2/index.html#/"
APP_NAME = "百度指数查询工具"
APP_VERSION = "2.4.0"


def _account_path() -> pathlib.Path:
    if sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home() / "AppData" / "Roaming")
    else:
        base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    return base / "CrossPlatformTools" / "百度指数跨平台查询工具" / "accounts.json"


def _cookie_pairs(header: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in (header or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            result[name] = value
    return result


def _has_login_cookie(header: str) -> bool:
    values = _cookie_pairs(header)
    return bool(values.get("BDUSS") or values.get("BDUSS_BFESS"))


def _normalise_accounts(values: str | Sequence[str]) -> List[str]:
    raw = values.splitlines() if isinstance(values, str) else values
    result: List[str] = []
    seen = set()
    for item in raw:
        value = str(item or "").strip()
        if value and _has_login_cookie(value) and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _merge_cookie_headers(account: str, browser: str) -> str:
    """Add current browser/device cookies without replacing account tokens."""
    values = _cookie_pairs(browser)
    values.update(_cookie_pairs(account))
    return "; ".join("%s=%s" % item for item in sorted(values.items()))


def _row_dict(row: IndexPoint | BrandIndexPoint) -> Dict[str, Any]:
    # Preserve missing fields as null so the interface can distinguish “no
    # value returned” from the numeric value zero.
    return dataclasses.asdict(row)


class SystemWebViewApi:
    """Backend for the native system-WebView desktop release."""

    def __init__(self, demo: bool = False, sleep_fn=time.sleep):
        self._demo = demo
        self._sleep_fn = sleep_fn
        self._main_window = None
        self._login_window = None
        self._signature_window = None
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._accounts_lock = threading.Lock()
        self._signature_lock = threading.Lock()
        self._login_window_lock = threading.Lock()
        self._login_create_lock = threading.Lock()
        self._login_window_factory: Optional[Callable[[], Any]] = None
        self._shutting_down = False

    def bind_windows(self, main_window, login_window, signature_window=None) -> None:
        self._main_window = main_window
        self._login_window = login_window
        self._signature_window = signature_window

    def set_login_window_factory(self, factory: Callable[[], Any]) -> None:
        self._login_window_factory = factory

    @staticmethod
    def _window_is_closed(window: Any) -> bool:
        closed = getattr(getattr(window, "events", None), "closed", None)
        try:
            return bool(closed and closed.is_set())
        except Exception:
            return False

    def release_login_window(self, window: Any) -> None:
        with self._login_window_lock:
            if self._login_window is window:
                self._login_window = None
            if self._signature_window is window:
                self._signature_window = None

    def _current_login_window(self) -> Any:
        with self._login_window_lock:
            window = self._login_window
        if window is not None and self._window_is_closed(window):
            self.release_login_window(window)
            return None
        return window

    def _ensure_login_window(self) -> Any:
        window = self._current_login_window()
        if window is not None or self._shutting_down or not self._login_window_factory:
            return window
        with self._login_create_lock:
            window = self._current_login_window()
            if window is not None or self._shutting_down:
                return window
            window = self._login_window_factory()
            if window is not None:
                with self._login_window_lock:
                    self._login_window = window
                    self._signature_window = window
            return window

    @staticmethod
    def _wait_for_window(window: Any, timeout: float = 15.0) -> bool:
        loaded = getattr(getattr(window, "events", None), "loaded", None)
        if not loaded:
            return True
        try:
            return bool(loaded.wait(timeout))
        except Exception:
            return False

    def shutdown(self) -> None:
        self._shutting_down = True
        window = self._current_login_window()
        if window is None:
            return
        self.release_login_window(window)
        try:
            window.destroy()
        except Exception:
            pass

    def _emit(self, name: str, payload: Dict[str, Any]) -> None:
        if not self._main_window:
            return
        script = "window.__pythonEvent && window.__pythonEvent(%s,%s)" % (
            json.dumps(name, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
        )
        try:
            self._main_window.evaluate_js(script)
        except Exception:
            pass

    def _load_account_document(self) -> Dict[str, Any]:
        try:
            value = json.loads(_account_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "cookies": _normalise_accounts(value.get("cookies", [])),
            "strategy": value.get("strategy") if value.get("strategy") in ("round_robin", "random") else "round_robin",
        }

    def _save_account_document(self, values: Sequence[str], strategy: str = "round_robin") -> None:
        path = _account_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "cookies": _normalise_accounts(values),
            "strategy": strategy if strategy in ("round_robin", "random") else "round_robin",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(path)

    def bootstrap(self) -> Dict[str, Any]:
        accounts = self._load_account_document()
        regions = [dict(dataclasses.asdict(item), display_name=item.display_name) for item in all_regions()]
        return {
            "version": APP_VERSION,
            "accounts": "\n".join(accounts["cookies"]),
            "strategy": accounts["strategy"],
            "regions": regions,
            "demo": self._demo,
            "outputDir": str(pathlib.Path.home() / "Downloads" / "百度指数数据"),
        }

    def load_accounts(self) -> Dict[str, Any]:
        value = self._load_account_document()
        return {"text": "\n".join(value["cookies"]), "count": len(value["cookies"]), "strategy": value["strategy"]}

    def save_accounts(self, text: str, strategy: str = "round_robin") -> Dict[str, Any]:
        values = _normalise_accounts(text)
        with self._accounts_lock:
            self._save_account_document(values, strategy)
        return {"count": len(values), "text": "\n".join(values), "strategy": strategy}

    def open_login(self) -> Dict[str, Any]:
        last_error = ""
        for attempt in range(2):
            try:
                window = self._ensure_login_window()
            except Exception as exc:
                last_error = str(exc)
                window = None
            if window is None:
                if attempt == 0:
                    continue
                break
            try:
                window.load_url(LOGIN_URL)
                window.show()
                return {"opened": True, "message": "登录窗口已打开"}
            except Exception as exc:
                last_error = str(exc)
                self.release_login_window(window)
                if attempt == 0:
                    continue
        message = "登录窗口启动失败"
        if last_error:
            message += "：" + last_error
        return {"opened": False, "message": message}

    def close_login(self) -> Dict[str, Any]:
        window = self._current_login_window()
        if window:
            try:
                window.hide()
            except Exception:
                self.release_login_window(window)
        return {"closed": True}

    @staticmethod
    def _cookie_header(cookies: Sequence[Any]) -> str:
        selected: Dict[str, Tuple[Tuple[int, int, int, int], str]] = {}
        for cookie in cookies:
            # pywebview 6 returns one http.cookies.SimpleCookie per native
            # cookie. Older renderers may return attribute objects, so accept
            # both shapes and preserve HttpOnly values.
            if hasattr(cookie, "items"):
                entries = []
                for name, morsel in cookie.items():
                    entries.append((
                        str(name), str(getattr(morsel, "value", "") or ""),
                        str(morsel["domain"] or ""), str(morsel["path"] or "/"),
                    ))
            else:
                entries = [(
                    str(getattr(cookie, "name", "") or ""),
                    str(getattr(cookie, "value", "") or ""),
                    str(getattr(cookie, "domain", "") or ""),
                    str(getattr(cookie, "path", "/") or "/"),
                )]
            for name, value, raw_domain, path in entries:
                domain = raw_domain.lstrip(".").lower()
                if domain != "baidu.com" and not domain.endswith(".baidu.com"):
                    continue
                # Prefer the root .baidu.com cookie over a same-name cookie
                # scoped to passport.baidu.com or another subdomain. The
                # resulting header is used for index.baidu.com API requests.
                score = (
                    1 if domain == "baidu.com" else 0,
                    -domain.count("."),
                    1 if path == "/" else 0,
                    -len(path),
                )
                if name and (name not in selected or score >= selected[name][0]):
                    selected[name] = (score, value)
        return "; ".join("%s=%s" % (name, selected[name][1]) for name in sorted(selected))

    def sync_login_cookies(self) -> Dict[str, Any]:
        document = self._load_account_document()
        values = list(document["cookies"])
        try:
            window = self._ensure_login_window()
        except Exception as exc:
            window = None
            startup_error = str(exc)
        else:
            startup_error = ""
        if window is None:
            return {
                "text": "\n".join(values), "count": len(values),
                "strategy": document["strategy"], "detected": False,
                "message": "登录窗口尚未启动" + (("：" + startup_error) if startup_error else ""),
            }

        self._wait_for_window(window)
        header = ""
        last_error = ""
        for attempt in range(4):
            try:
                header = self._cookie_header(window.get_cookies() or [])
            except Exception as exc:
                last_error = str(exc)
                self.release_login_window(window)
                if attempt == 0:
                    try:
                        window = self._ensure_login_window()
                    except Exception as recreate_error:
                        last_error = str(recreate_error)
                        window = None
                    if window is not None:
                        self._wait_for_window(window)
                        continue
                break
            if _has_login_cookie(header):
                break
            if attempt < 3:
                self._sleep_fn(0.4)

        if _has_login_cookie(header):
            key = _cookie_pairs(header).get("BDUSS_BFESS") or _cookie_pairs(header).get("BDUSS")
            replaced = False
            for index, existing in enumerate(values):
                existing_key = _cookie_pairs(existing).get("BDUSS_BFESS") or _cookie_pairs(existing).get("BDUSS")
                if existing_key == key:
                    values[index] = header
                    replaced = True
                    break
            if not replaced:
                values.append(header)
            self._save_account_document(values, document["strategy"])
            try:
                window.hide()
                if self._main_window:
                    self._main_window.show()
            except Exception:
                pass
            message = "Cookie 已提取并保存"
        else:
            names = sorted(_cookie_pairs(header))
            message = "未读取到 BDUSS 登录 Cookie"
            if names:
                message += "；当前仅检测到：" + "、".join(names)
            elif last_error:
                message += "：" + last_error
            else:
                message += "；请确认页面显示已登录后再同步"
        return {
            "text": "\n".join(values), "count": len(values),
            "strategy": document["strategy"], "detected": _has_login_cookie(header),
            "message": message,
        }

    def _signature(self, timeout: float = 20.0) -> Tuple[str, str]:
        target = self._signature_window
        if target is not None and self._window_is_closed(target):
            self.release_login_window(target)
            target = None
        target = target or self._current_login_window() or self._ensure_login_window()
        if not target:
            raise RuntimeError("查询组件尚未启动")
        self._wait_for_window(target, timeout)
        with self._signature_lock:
            last: Dict[str, Any] = {}
            # The dedicated off-screen page is loaded at application start. A
            # single automatic reload covers slow first launches and network
            # changes without asking the user to reopen the login window.
            for attempt in range(2):
                key = "__baiduIndexPythonSign_" + uuid.uuid4().hex
                script = r"""
                (function(key) {
                  const sources = Array.from(document.scripts).map(s => s.src).filter(Boolean);
                  if (!window.Paris || typeof window.Paris.getAcsInstance !== 'function') {
                    return JSON.stringify({state:'WAIT', loaded:document.readyState, hasScript:sources.some(s => s.includes('heicha')), userAgent:navigator.userAgent});
                  }
                  if (!window[key]) {
                    window[key] = 'PENDING';
                    window.Paris.getAcsInstance((error, instance) => {
                      if (error || !instance || typeof instance.getSign !== 'function') { window[key] = 'ERROR'; return; }
                      instance.getSign((signError, sign) => { window[key] = signError ? 'ERROR' : String(sign || 'ERROR'); });
                    });
                  }
                  const value = window[key];
                  return JSON.stringify({state:value === 'PENDING' ? 'WAIT' : 'READY', value:value,
                    hasScript:sources.some(s => s.includes('heicha')), userAgent:navigator.userAgent});
                })(%s)
                """ % json.dumps(key)
                deadline = time.monotonic() + max(3.0, timeout)
                while time.monotonic() < deadline:
                    try:
                        value = target.evaluate_js(script)
                    except Exception:
                        value = None
                    try:
                        last = json.loads(value) if isinstance(value, str) else (value or {})
                    except (TypeError, ValueError):
                        last = {}
                    if last.get("state") == "READY":
                        sign = str(last.get("value") or "")
                        if sign and sign != "ERROR":
                            return sign, str(last.get("userAgent") or "")
                        break
                    if self._cancel_event.wait(0.25):
                        raise InterruptedError("任务已停止")
                if attempt == 0:
                    self._emit("log", {"text": "查询组件正在重新初始化…"})
                    try:
                        target.load_url(LOGIN_URL)
                    except Exception:
                        pass
                    if self._cancel_event.wait(1.0):
                        raise InterruptedError("任务已停止")
            detail = "查询组件初始化超时" if not last.get("hasScript") else "查询签名生成超时"
            raise RuntimeError(detail)

    def _wait(self, seconds: float, label: str) -> None:
        duration = max(0.0, float(seconds))
        if not duration:
            self._emit("countdown", {"seconds": 0, "label": label})
            return
        deadline = time.monotonic() + duration
        last = None
        while True:
            while self._pause_event.is_set():
                if self._cancel_event.wait(0.1):
                    raise InterruptedError("任务已停止")
                deadline += 0.1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            current = max(1, int(math.ceil(remaining)))
            if current != last:
                last = current
                self._emit("countdown", {"seconds": current, "label": label})
            if self._cancel_event.wait(min(0.1, remaining)):
                raise InterruptedError("任务已停止")
        self._emit("countdown", {"seconds": 0, "label": label})

    def preview_countdown(self, seconds: float = 4.0) -> Dict[str, Any]:
        """Run the same countdown path used immediately before live requests."""
        started = time.monotonic()
        self._wait(seconds, "提取间隔显示测试")
        return {"seconds": float(seconds), "elapsed": time.monotonic() - started}

    def _client(self, cookie: str, interval: float) -> BaiduIndexClient:
        return BaiduIndexClient(cookie, timeout=25, retries=2, retry_delay=max(2.0, interval), signature_provider=self._signature)

    @staticmethod
    def _account_order(count: int, task_index: int, strategy: str) -> List[int]:
        if strategy == "random":
            import random
            start = random.SystemRandom().randrange(count)
        else:
            start = task_index % count
        return [(start + offset) % count for offset in range(count)]

    def _query_search(self, config: Dict[str, Any], cookies: Sequence[str], regions: Sequence[Region]) -> List[IndexPoint]:
        rows: List[IndexPoint] = []
        keywords = config["keywords"]
        interval = float(config.get("interval") or 0)
        strategy = str(config.get("strategy") or "round_robin")
        channels = set(config.get("searchChannels") or [])
        include_news = "news" in channels
        total = max(1, len(keywords) * len(regions))
        done = 0
        for region in regions:
            for keyword in keywords:
                last_error: Optional[Exception] = None
                batch: List[IndexPoint] = []
                account_index = 0
                for position, index in enumerate(self._account_order(len(cookies), done, strategy)):
                    label = "搜索指数 · %s · %s" % (keyword, region.display_name)
                    self._wait(interval, label)
                    try:
                        batch = self._client(cookies[index], interval).query_keyword(
                            keyword, config["startDate"], config["endDate"], region,
                        )
                        account_index = index
                        break
                    except (AuthenticationError, RateLimitError) as exc:
                        last_error = exc
                        if position + 1 < len(cookies):
                            self._emit("log", {"text": "账号受限，正在切换下一账号"})
                else:
                    if last_error:
                        raise last_error
                batch = [replace(
                    item,
                    overall=item.overall if "overall" in channels else None,
                    pc=item.pc if "pc" in channels else None,
                    mobile=item.mobile if "mobile" in channels else None,
                ) for item in batch]
                if include_news:
                    label = "资讯指数 · %s · %s" % (keyword, region.display_name)
                    self._wait(interval, label)
                    try:
                        news = self._client(cookies[account_index], interval).query_news_keyword(
                            keyword, config["startDate"], config["endDate"], region,
                        )
                    except NoDataError:
                        news = []
                    news_by_date = {item.date: item.news for item in news}
                    batch = [replace(item, news=news_by_date.get(item.date)) for item in batch]
                rows.extend(batch)
                done += 1
                self._emit("batch", {"rows": [_row_dict(item) for item in batch]})
                self._emit("progress", {"done": done, "total": total, "label": "%s · %s" % (keyword, region.display_name)})
        return rows

    def _query_brand(self, config: Dict[str, Any], cookies: Sequence[str], regions: Sequence[Region]) -> List[BrandIndexPoint]:
        rows: List[BrandIndexPoint] = []
        keywords = config["keywords"]
        interval = float(config.get("interval") or 0)
        strategy = str(config.get("strategy") or "round_robin")
        metrics = list(config.get("brandChannels") or [])
        labels = {"brand_index": "品牌指数", "brand_search": "品牌搜索指数", "brand_news": "品牌资讯指数", "brand_interaction": "品牌互动指数"}
        request_total = max(1, len(keywords) + len(keywords) * len(regions) * len(metrics))
        request_done = 0
        task_done = 0
        entities = {}

        for region in regions:
            for keyword_index, keyword in enumerate(keywords):
                # Resolve lazily so the first keyword starts streaming metric
                # rows immediately; cache the entity for all later regions.
                if keyword not in entities:
                    logical_label = "品牌匹配 · %s" % keyword
                    self._emit("log", {"text": "品牌查询 %d/%d · %s" % (request_done + 1, request_total, logical_label)})
                    last_error: Optional[Exception] = None
                    for position, index in enumerate(self._account_order(len(cookies), keyword_index, strategy)):
                        if position:
                            self._emit("log", {"text": "品牌匹配切换账号 %d/%d · %s" % (position + 1, len(cookies), keyword)})
                        self._wait(interval, logical_label)
                        try:
                            entities[keyword] = self._client(cookies[index], interval).resolve_brand(keyword)
                            break
                        except (AuthenticationError, RateLimitError) as exc:
                            last_error = exc
                    else:
                        if last_error:
                            raise last_error
                        raise NoDataError("未匹配到品牌：%s" % keyword)
                    request_done += 1
                    self._emit("progress", {"done": request_done, "total": request_total, "label": logical_label})
                entity = entities[keyword]
                merged: Dict[str, BrandIndexPoint] = {}
                for metric_index, metric in enumerate(metrics):
                    label = "%s · %s · %s" % (labels[metric], entity.name, region.display_name)
                    self._emit("log", {"text": "品牌查询 %d/%d · %s" % (request_done + 1, request_total, label)})
                    metric_rows: List[BrandIndexPoint] = []
                    last_error: Optional[Exception] = None
                    order = self._account_order(len(cookies), task_done + metric_index, strategy)
                    for position, index in enumerate(order):
                        if position:
                            self._emit("log", {"text": "%s切换账号 %d/%d" % (labels[metric], position + 1, len(order))})
                        self._wait(interval, label)
                        try:
                            metric_rows = self._client(cookies[index], interval).query_brand_metric(
                                entity, metric, config["startDate"], config["endDate"], region,
                            )
                            last_error = None
                            break
                        except NoDataError:
                            last_error = None
                            metric_rows = []
                            break
                        except (AuthenticationError, RateLimitError) as exc:
                            last_error = exc
                    if last_error:
                        raise last_error
                    for item in metric_rows:
                        existing = merged.get(item.date)
                        if existing is None:
                            merged[item.date] = item
                        else:
                            merged[item.date] = replace(
                                existing,
                                brand_index=item.brand_index if item.brand_index is not None else existing.brand_index,
                                brand_search=item.brand_search if item.brand_search is not None else existing.brand_search,
                                brand_news=item.brand_news if item.brand_news is not None else existing.brand_news,
                                brand_interaction=item.brand_interaction if item.brand_interaction is not None else existing.brand_interaction,
                            )
                    request_done += 1
                    partial = [merged[key] for key in sorted(merged)]
                    self._emit("snapshot", {"rows": [_row_dict(item) for item in rows + partial]})
                    self._emit("progress", {"done": request_done, "total": request_total, "label": label})
                batch = [merged[key] for key in sorted(merged)]
                rows.extend(batch)
                task_done += 1
        return rows

    def _export(self, rows: Sequence[IndexPoint | BrandIndexPoint], config: Dict[str, Any]) -> Tuple[List[str], str]:
        output_root = pathlib.Path(config.get("outputDir") or pathlib.Path.home() / "Downloads" / "百度指数数据").expanduser()
        run_dir, stamp = create_run_directory(output_root)
        period = str(config.get("period") or "daily")
        start_date, end_date = config["startDate"], config["endDate"]
        index_mode = config.get("indexMode") or "search"
        if index_mode == "brand":
            aggregated = aggregate_brand_points(rows, period, "mean")
            path = run_dir / output_filename("all", stamp, period, start_date, end_date, kind="brand")
            return [str(write_brand_csv(aggregated, path))], str(run_dir)
        aggregated = aggregate_points(rows, period, "mean")
        outputs = []
        channels = set(config.get("searchChannels") or [])
        if channels.intersection({"overall", "pc", "mobile"}):
            path = run_dir / output_filename("all", stamp, period, start_date, end_date)
            outputs.append(str(write_csv(aggregated, path)))
        if "news" in channels:
            path = run_dir / output_filename("all", stamp, period, start_date, end_date, kind="news")
            outputs.append(str(write_news_csv(aggregated, path)))
        return outputs, str(run_dir)

    def query(self, config: Dict[str, Any]) -> Dict[str, Any]:
        self._cancel_event.clear()
        self._pause_event.clear()
        document = self._load_account_document()
        cookies = list(document["cookies"])
        # BAIDUID and related device cookies can rotate independently from the
        # account token. Merge the native WebView's current values before each
        # request so a saved account remains consistent with the signature
        # browser and multi-account tokens stay distinct.
        try:
            login_window = self._ensure_login_window()
            browser_cookie = self._cookie_header(login_window.get_cookies() or []) if login_window else ""
        except Exception:
            browser_cookie = ""
        if browser_cookie:
            cookies = [_merge_cookie_headers(account, browser_cookie) for account in cookies]
        keywords = [str(item).strip() for item in config.get("keywords", []) if str(item).strip()]
        region_ids = config.get("regionIds") or [0]
        regions = [resolve_region(int(value)) for value in region_ids]
        index_mode = "brand" if config.get("indexMode") == "brand" else "search"
        config = dict(config)
        config["keywords"] = keywords
        config["strategy"] = document["strategy"]
        channels = config.get("brandChannels") if index_mode == "brand" else config.get("searchChannels")
        validate_task(
            keywords, regions, config["startDate"], config["endDate"],
            has_cookie=bool(cookies), has_channel=bool(channels),
            include_news="news" in (channels or []),
        )
        try:
            rows = self._query_brand(config, cookies, regions) if index_mode == "brand" else self._query_search(config, cookies, regions)
            outputs, run_dir = self._export(rows, config)
            result_rows = [_row_dict(item) for item in rows]
            return {"cancelled": False, "rows": result_rows, "outputs": outputs, "runDir": run_dir}
        except AuthenticationError as exc:
            raise RuntimeError(
                "账号 Cookie 未通过查询接口校验；请打开“账号登录与 Cookie”，"
                "在软件内登录窗口完成登录后点击“同步 Cookie”"
            ) from exc
        except InterruptedError:
            return {"cancelled": True, "rows": [], "outputs": [], "runDir": ""}

    def cancel(self) -> Dict[str, Any]:
        self._cancel_event.set()
        self._pause_event.clear()
        return {"cancelled": True}

    def toggle_pause(self) -> Dict[str, Any]:
        if self._pause_event.is_set():
            self._pause_event.clear()
        else:
            self._pause_event.set()
        return {"paused": self._pause_event.is_set()}

    def open_path(self, path: str) -> Dict[str, Any]:
        target = str(pathlib.Path(path).expanduser())
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        elif os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", target])
        return {"opened": target}

    def choose_output_directory(self, current: str = "") -> Dict[str, Any]:
        import webview

        initial = pathlib.Path(current or pathlib.Path.home() / "Downloads").expanduser()
        if not initial.is_dir():
            initial = pathlib.Path.home() / "Downloads"
        selected = self._main_window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=str(initial),
            allow_multiple=False,
        ) if self._main_window else None
        path = str(pathlib.Path(selected[0]).expanduser()) if selected else str(initial)
        return {"selected": bool(selected), "path": path}


def _ui_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "webview_ui" / "index.html"


def launch_system_webview(
    demo: bool = False,
    auto_close_ms: int = 0,
    folder_dialog_demo: bool = False,
    ui_self_test_output: str = "",
) -> int:
    import webview

    api = SystemWebViewApi(demo=demo)
    main_window = webview.create_window(
        APP_NAME,
        _ui_path().as_uri(), js_api=api,
        width=1520, height=940, min_size=(1120, 700),
        background_color="#f4f7fb",
    )
    api.bind_windows(main_window, None, None)

    def create_login_page():
        window = webview.create_window(
            "百度账号登录",
            LOGIN_URL, width=1100, height=800, min_size=(760, 560), hidden=True,
        )
        if window:
            window.events.closed += lambda: api.release_login_window(window)
        return window

    api.set_login_window_factory(create_login_page)
    main_window.events.closed += api.shutdown

    def started() -> None:
        def prepare_login_page() -> None:
            try:
                main_window.events.loaded.wait(15)
                if not main_window.events.loaded.is_set():
                    return
                api._ensure_login_window()
                if demo and not auto_close_ms:
                    time.sleep(0.8)
                    api.preview_countdown(4)
            except Exception:
                pass
            if folder_dialog_demo:
                try:
                    time.sleep(0.8)
                    api.choose_output_directory(str(pathlib.Path.home() / "Downloads"))
                except Exception:
                    pass
        threading.Thread(target=prepare_login_page, daemon=True).start()
        if ui_self_test_output:
            def run_ui_self_test() -> None:
                output = pathlib.Path(ui_self_test_output).expanduser()
                try:
                    main_window.events.loaded.wait(15)
                    main_window.resize(1120, 700)
                    time.sleep(0.6)
                    ready_deadline = time.monotonic() + 15.0
                    while time.monotonic() < ready_deadline:
                        if main_window.evaluate_js("Boolean(window.__appReady)"):
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("interface bootstrap timeout")
                    login_window_recreated = False
                    try:
                        first_login = api._ensure_login_window()
                        first_opened = api.open_login().get("opened", False)
                        if first_login:
                            first_login.destroy()
                            first_login.events.closed.wait(5)
                        second_opened = api.open_login().get("opened", False)
                        second_login = api._current_login_window()
                        login_window_recreated = bool(
                            first_opened and second_opened and second_login and second_login is not first_login
                        )
                        if second_login:
                            second_login.hide()
                    except Exception:
                        login_window_recreated = False
                    value = main_window.evaluate_js(r"""
                    (function () {
                      const visible = element => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
                      };
                      const checks = {};
                      checks.title = document.title === '百度指数查询工具';
                      checks.noEngineLabel = !document.getElementById('engine') && !document.body.innerText.includes('系统 WebView');
                      checks.navigation = [...document.querySelectorAll('.navItem b')].map(x => x.textContent).join('|') === '查询任务|账号中心|导出记录';
                      checks.navigationSingleLine = [...document.querySelectorAll('.navItem b')].every(x => {
                        const style = getComputedStyle(x);
                        return style.whiteSpace === 'nowrap' && x.scrollWidth <= x.parentElement.clientWidth;
                      });
                      checks.navigationInsideSidebar = [...document.querySelectorAll('.navItem')].every(x => {
                        const item = x.getBoundingClientRect(), nav = x.parentElement.getBoundingClientRect();
                        return item.left >= nav.left && item.right <= nav.right;
                      });
                      checks.noSettings = !document.querySelector('[data-page="settings"]');
                      checks.noExtraCopy = !document.body.innerText.includes('经济与品牌指数数据工作台') && !document.body.innerText.includes('设计预览') && !document.body.innerText.includes('经' + '数');
                      const sizes = [...document.querySelectorAll('body *')].filter(visible).map(x => parseFloat(getComputedStyle(x).fontSize)).filter(Number.isFinite);
                      const rawMinimumFont = Math.min(...sizes);
                      const effectiveMinimumFont = rawMinimumFont * Math.max(1, Number(devicePixelRatio) || 1);
                      checks.minimumFont = effectiveMinimumFont >= 12;
                      checks.startVisible = (() => { const r = document.getElementById('startBtn').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })();
                      checks.noHorizontalOverflow = document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth;
                      checks.configNoHorizontalOverflow = (() => {
                        const element = document.querySelector('.configScroll');
                        return element.scrollWidth <= element.clientWidth;
                      })();
                      checks.statusControlsInside = (() => {
                        const r = document.querySelector('.runControls').getBoundingClientRect();
                        return r.left >= 0 && r.right <= innerWidth;
                      })();
                      checks.resultMetaSeparate = !document.getElementById('resultMeta').textContent.includes('已选地区') && !document.getElementById('resultMeta').querySelector('#selectedRegions');
                      checks.statusSingleLine = (() => {
                        const title = document.getElementById('statusTitle').getBoundingClientRect();
                        const detail = document.getElementById('status').getBoundingClientRect();
                        return Math.abs(title.top - detail.top) < 3;
                      })();
                      checks.regionsBelowStatus = (() => {
                        const top = document.querySelector('.statusTop').getBoundingClientRect();
                        const row = document.querySelector('.selectedRegionsRow').getBoundingClientRect();
                        return visible(document.querySelector('.selectedRegionsRow')) && row.top >= top.bottom;
                      })();
                      checks.regionsFullyAccessible = (() => {
                        const element = document.getElementById('selectedRegions'), style = getComputedStyle(element);
                        return style.whiteSpace === 'normal' && ['auto', 'scroll'].includes(style.overflowY) && !element.textContent.includes('…');
                      })();
                      checks.missingDash = [...document.querySelectorAll('#tableBody td')].some(x => x.textContent.trim() === '—');
                      document.getElementById('brandTab').click();
                      checks.brandMode = !document.getElementById('brandChannels').classList.contains('hidden') && document.getElementById('tableHead').innerText.includes('品牌互动指数');
                      document.getElementById('searchTab').click();
                      checks.searchMode = !document.getElementById('searchChannels').classList.contains('hidden') && document.getElementById('tableHead').innerText.includes('PC端');
                      document.getElementById('loginBtn').click();
                      checks.accountOpen = !document.getElementById('accountModal').classList.contains('hidden');
                      checks.accountDialogInside = (() => {
                        const r = document.querySelector('.dialog').getBoundingClientRect();
                        return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
                      })();
                      document.getElementById('closeModal').click();
                      checks.accountClose = document.getElementById('accountModal').classList.contains('hidden');
                      checks.noCommunityNavigation = !document.querySelector('[data-page="community"]');
                      document.getElementById('aboutButton').click();
                      checks.aboutOpen = !document.getElementById('aboutModal').classList.contains('hidden');
                      checks.aboutVersion = document.getElementById('aboutModal').innerText.includes('版本：v2.4.0');
                      checks.aboutContact = document.getElementById('aboutModal').innerText.includes('QQ群：610645081');
                      checks.aboutIcon = (() => {
                        const icon = document.querySelector('#aboutButton .aboutIcon');
                        if (!icon) return false;
                        const box = icon.getBoundingClientRect();
                        return box.width >= 20 && box.height >= 20 && icon.textContent.trim() === 'i';
                      })();
                      document.getElementById('closeAbout').click();
                      checks.aboutClose = document.getElementById('aboutModal').classList.contains('hidden');
                      document.getElementById('collapseLog').click();
                      checks.logCollapse = document.body.classList.contains('logCollapsed');
                      document.getElementById('collapseLog').click();
                      checks.logExpand = !document.body.classList.contains('logCollapsed');
                      window.__pythonEvent('countdown', {seconds: 4, label: '界面自测'});
                      checks.countdown = document.getElementById('countdownValue').textContent === '4';
                      window.__pythonEvent('progress', {done: 3, total: 5, label: '界面自测'});
                      checks.progress = document.getElementById('percent').textContent === '60%';
                      document.getElementById('clearLog').click();
                      checks.logClear = document.querySelectorAll('#activityLog .logItem').length === 1;
                      checks.regionActionLabels = document.getElementById('selectProvinces').textContent === '选择全部省份' && document.getElementById('selectCities').textContent === '选择全部城市' && document.getElementById('clearRegions').textContent === '清空';
                      document.getElementById('selectProvinces').click();
                      checks.selectAllProvinces = [...document.querySelectorAll('.regionCheck')].every(box => {
                        const region = regions.find(x => Number(x.area_id) === Number(box.value));
                        return box.checked === Boolean(region && region.kind === 'province');
                      });
                      checks.allProvinceNamesShown = document.getElementById('selectedRegions').textContent === regions.filter(x => x.kind === 'province').map(x => x.display_name).join('、');
                      document.getElementById('selectCities').click();
                      checks.selectAllCities = [...document.querySelectorAll('.regionCheck')].every(box => {
                        const region = regions.find(x => Number(x.area_id) === Number(box.value));
                        return box.checked === Boolean(region && region.kind === 'city');
                      });
                      document.getElementById('clearRegions').click();
                      checks.clearRegions = !document.querySelector('.regionCheck:checked');
                      document.getElementById('resetConfig').click();
                      checks.reset = document.getElementById('keywords').value === '华为' && document.getElementById('interval').value === '4';
                      checks.outputDirectory = Boolean(document.getElementById('outputDir').value);
                      checks.queryControls = document.getElementById('pauseBtn').disabled && document.getElementById('stopBtn').disabled;
                      return JSON.stringify({allPass: Object.values(checks).every(Boolean), minimumFont: effectiveMinimumFont, rawMinimumFont, devicePixelRatio, checks});
                    })()
                    """)
                    payload = json.loads(value) if isinstance(value, str) else value
                    if not isinstance(payload, dict):
                        payload = {"allPass": False, "error": "invalid UI self-test result"}
                    checks = payload.setdefault("checks", {})
                    checks["loginWindowRecreated"] = login_window_recreated
                    payload["allPass"] = bool(payload.get("allPass")) and login_window_recreated
                    text = json.dumps(payload, ensure_ascii=False)
                except Exception as exc:
                    text = json.dumps({"allPass": False, "error": repr(exc)}, ensure_ascii=False)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(text, encoding="utf-8")
                api.shutdown()
                try:
                    main_window.destroy()
                except Exception:
                    pass
            threading.Thread(target=run_ui_self_test, daemon=True).start()
        if auto_close_ms:
            def close_later() -> None:
                time.sleep(max(0.2, auto_close_ms / 1000.0))
                api.shutdown()
                try:
                    main_window.destroy()
                except Exception:
                    pass
            threading.Thread(target=close_later, daemon=True).start()

    storage = _account_path().parent / "browser-profile"
    storage.mkdir(parents=True, exist_ok=True)
    gui = "edgechromium" if os.name == "nt" else "cocoa" if sys.platform == "darwin" else None
    try:
        webview.start(started, gui=gui, debug=False, private_mode=False, storage_path=str(storage))
    finally:
        api.shutdown()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="百度指数图形界面")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--auto-close-ms", type=int, default=0)
    parser.add_argument("--folder-dialog-demo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ui-self-test-output", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return launch_system_webview(args.demo, args.auto_close_ms, args.folder_dialog_demo, args.ui_self_test_output)


if __name__ == "__main__":
    raise SystemExit(main())
