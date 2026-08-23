from __future__ import annotations

import json
import ssl
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class NetworkError(RuntimeError):
    pass


class HttpError(NetworkError):
    def __init__(self, status: int, message: str):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = status


class UrlLibTransport:
    def __init__(self, timeout: float = 20.0, ca_file: Optional[str] = None):
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        query = urlencode(params, doseq=True)
        full_url = url + ("&" if "?" in url else "?") + query
        request = Request(full_url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=self.timeout, context=self.context) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            raise HttpError(exc.code, body) from exc
        except (URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            raise NetworkError(str(exc)) from exc
        try:
            value = json.loads(raw.decode(charset, "replace"))
        except (ValueError, UnicodeError) as exc:
            raise NetworkError("服务器返回的内容不是 JSON") from exc
        if not isinstance(value, dict):
            raise NetworkError("服务器 JSON 顶层不是对象")
        return value

    def post_json(
        self,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        request_headers = dict(headers)
        request_headers.setdefault("Content-Type", "application/json; charset=UTF-8")
        request_headers.setdefault("Origin", "https://index.baidu.com")
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout, context=self.context) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except HTTPError as exc:
            content = exc.read().decode("utf-8", "replace")[:500]
            raise HttpError(exc.code, content) from exc
        except (URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            raise NetworkError(str(exc)) from exc
        try:
            value = json.loads(raw.decode(charset, "replace"))
        except (ValueError, UnicodeError) as exc:
            raise NetworkError("服务器返回的内容不是 JSON") from exc
        if not isinstance(value, dict):
            raise NetworkError("服务器 JSON 顶层不是对象")
        return value
