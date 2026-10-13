from __future__ import annotations

import json
import ssl
import sys
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class NetworkError(RuntimeError):
    pass


class TLSCertificateError(NetworkError):
    pass


def create_tls_context(ca_file: Optional[str] = None):
    """Use desktop OS trust, never the build machine's OpenSSL certificate path."""
    if ca_file is not None:
        return ssl.create_default_context(cafile=ca_file)
    if sys.platform in ("darwin", "win32"):
        try:
            import truststore
        except ImportError as exc:
            raise NetworkError("系统证书组件缺失，请重新安装完整发行包") from exc
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        return context
    return ssl.create_default_context()


def _network_error(exc: Exception) -> NetworkError:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return TLSCertificateError("HTTPS 证书验证失败，请检查系统日期及网络代理证书是否已受系统信任")
    return NetworkError(str(exc))


def check_https() -> Dict[str, Any]:
    """Frozen-app smoke check, without account credentials or unverified TLS."""
    transport = UrlLibTransport(timeout=25)
    context = transport.context
    url = "https://index.baidu.com/v2/index.html"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    try:
        with urlopen(request, timeout=transport.timeout, context=context) as response:
            status = response.status
            response.read(128)
    except HTTPError as exc:
        # An authenticated HTTP error still proves the TLS handshake succeeded.
        status = exc.code
        exc.close()
    result = {"success": True, "url": url, "httpStatus": status,
              "certificateRequired": context.verify_mode == ssl.CERT_REQUIRED,
              "hostnameVerified": context.check_hostname,
              "tlsBackend": type(context).__module__, "frozen": bool(getattr(sys, "frozen", False))}
    if not result["certificateRequired"] or not result["hostnameVerified"]:
        raise NetworkError("HTTPS 验证配置不完整")
    return result


class HttpError(NetworkError):
    def __init__(self, status: int, message: str):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = status


class UrlLibTransport:
    def __init__(self, timeout: float = 20.0, ca_file: Optional[str] = None):
        self.timeout = timeout
        self.context = create_tls_context(ca_file)

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
            raise _network_error(exc) from exc
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
            raise _network_error(exc) from exc
        try:
            value = json.loads(raw.decode(charset, "replace"))
        except (ValueError, UnicodeError) as exc:
            raise NetworkError("服务器返回的内容不是 JSON") from exc
        if not isinstance(value, dict):
            raise NetworkError("服务器 JSON 顶层不是对象")
        return value
