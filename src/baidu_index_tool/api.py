from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .crypto import decrypt_series
from .brand_regions import brand_area_id
from .models import BrandEntity, BrandIndexPoint, IndexPoint, Region
from .regions import resolve_region
from .reverse_contract import (
    BRAND_PATHS,
    BRAND_SUG_PATH,
    NEWS_PATH,
    PTBK_PATHS,
    REFERER,
    SEARCH_EARLIEST,
    SEARCH_PATH,
    STATUS_BAD_REQUEST,
    STATUS_NOT_LOGGED_IN,
    STATUS_RATE_LIMIT,
)
from .transport import HttpError, NetworkError, UrlLibTransport


class ApiError(RuntimeError):
    pass


class AuthenticationError(ApiError):
    pass


class RateLimitError(ApiError):
    pass


class RequestBlockedError(ApiError):
    """A server-side block applies to the request, not a verified account fault."""


class NoDataError(ApiError):
    pass


class BadRequestError(ApiError):
    pass


class ResponseFormatError(ApiError):
    pass


def parse_date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD: %s" % value) from exc


def validate_date_range(start_date: str, end_date: str) -> None:
    start, end = parse_date(start_date), parse_date(end_date)
    if start > end:
        raise ValueError("开始日期晚于结束日期")
    if start < SEARCH_EARLIEST:
        raise ValueError("搜索指数最早日期为 2006-06-01")
    if end >= dt.date.today():
        raise ValueError("结束日期最晚为昨天")


def _dates(start_date: str, count: int) -> List[str]:
    start = parse_date(start_date)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(count)]


def _daily_ranges(start_date: str, end_date: str):
    start, end = parse_date(start_date), parse_date(end_date)
    while start <= end:
        last = min(end, start + dt.timedelta(days=364))
        yield start.isoformat(), last.isoformat()
        start = last + dt.timedelta(days=1)


def _word_name(item: Mapping[str, Any], fallback: str) -> str:
    word = item.get("word")
    if isinstance(word, list) and word and isinstance(word[0], dict):
        return str(word[0].get("name") or fallback)
    if isinstance(word, str):
        return word
    return fallback


def _channel(item: Mapping[str, Any], *names: str) -> Mapping[str, Any]:
    for name in names:
        value = item.get(name)
        if isinstance(value, dict):
            return value
    return {}


def parse_search_payload(
    payload: Mapping[str, Any],
    ptbk: str,
    fallback_keyword: str,
    region: Region,
) -> List[IndexPoint]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    indexes = data.get("userIndexes") if isinstance(data, dict) else None
    if not isinstance(indexes, list):
        raise ResponseFormatError("返回数据中缺少 userIndexes")
    province, city = region.csv_location
    rows: List[IndexPoint] = []
    for item in indexes:
        if not isinstance(item, dict):
            raise ResponseFormatError("userIndexes 条目格式错误")
        if item.get("type", "day") not in (None, "day"):
            raise ResponseFormatError("服务端返回非日级采样（%s），请缩短查询时间范围" % item["type"])
        keyword = _word_name(item, fallback_keyword)
        all_info = _channel(item, "all", "_all")
        pc_info = _channel(item, "pc", "_pc")
        mobile_info = _channel(item, "wise", "mobile", "_wise")
        if not any(isinstance(item.get(name), dict) for name in ("all", "_all", "pc", "_pc", "wise", "mobile", "_wise")):
            raise ResponseFormatError("指数序列缺少通道字段")
        channels = []
        for info in (all_info, pc_info, mobile_info):
            encrypted = info.get("data", "") if isinstance(info, dict) else ""
            channels.append(decrypt_series(str(encrypted or ""), ptbk))
        count = max([len(x) for x in channels] or [0])
        if count == 0:
            continue
        start = str(all_info.get("startDate") or pc_info.get("startDate") or mobile_info.get("startDate") or "")
        if not start:
            raise ResponseFormatError("指数序列缺少 startDate")
        dates = _dates(start, count)
        padded = [series + [None] * (count - len(series)) for series in channels]
        for i, date in enumerate(dates):
            rows.append(IndexPoint(keyword, date, province, city, padded[0][i], padded[1][i], padded[2][i]))
    return rows


def parse_news_payload(
    payload: Mapping[str, Any],
    ptbk: str,
    fallback_keyword: str,
    region: Region,
) -> List[IndexPoint]:
    """Decode old and current FeedSearchApi envelopes with the ptbk table."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    indexes = data.get("index") if isinstance(data, dict) else None
    if not isinstance(indexes, list):
        rows = parse_search_payload(payload, ptbk, fallback_keyword, region)
        return [IndexPoint(x.keyword, x.date, x.province, x.city, None, None, None, x.overall) for x in rows]
    province, city = region.csv_location
    rows: List[IndexPoint] = []
    for item in indexes:
        if not isinstance(item, dict):
            continue
        if item.get("type", "day") not in (None, "day"):
            raise ResponseFormatError("资讯接口返回非日级采样（%s），请缩短查询时间范围" % item["type"])
        key = item.get("key")
        if isinstance(key, list) and key and isinstance(key[0], dict):
            keyword = str(key[0].get("name") or fallback_keyword)
        else:
            keyword = fallback_keyword
        series = decrypt_series(str(item.get("data") or item.get("_all") or ""), ptbk)
        start = str(item.get("startDate") or "")
        if series and not start:
            raise ResponseFormatError("资讯指数序列缺少 startDate")
        for date, value in zip(_dates(start, len(series)), series):
            rows.append(IndexPoint(keyword, date, province, city, None, None, None, value))
    return rows


class BaiduIndexClient:
    SEARCH_PATH = SEARCH_PATH
    NEWS_PATH = NEWS_PATH
    PTBK_PATHS = PTBK_PATHS
    BRAND_SUG_PATH = BRAND_SUG_PATH
    BRAND_PATHS = BRAND_PATHS

    def __init__(
        self,
        cookie: str,
        timeout: float = 20.0,
        retries: int = 2,
        retry_delay: float = 2.0,
        ca_file: Optional[str] = None,
        transport: Optional[Any] = None,
        base_url: str = "https://index.baidu.com",
        cipher_text: str = "",
        signature_provider: Optional[Callable[[], Any]] = None,
        user_agent: str = "",
        before_request: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
        on_no_data: Optional[Callable[[str, str, Region, str, str], None]] = None,
    ):
        cookie = (cookie or "").strip()
        if not cookie:
            raise ValueError("Cookie 为空")
        self.cookie = cookie
        self.retries = max(0, int(retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.transport = transport or UrlLibTransport(timeout=timeout, ca_file=ca_file)
        self.base_url = base_url.rstrip("/")
        self.cipher_text = (cipher_text or "").strip()
        self.signature_provider = signature_provider
        self.before_request = before_request
        self.on_no_data = on_no_data
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Cookie": cookie,
            "Pragma": "no-cache",
            "Referer": REFERER,
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _get(self, path: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        headers = dict(self.headers)
        if path in (self.SEARCH_PATH, self.NEWS_PATH):
            signature = self.cipher_text
            if self.signature_provider:
                generated = self.signature_provider()
                if isinstance(generated, tuple):
                    signature, user_agent = generated
                    if user_agent:
                        headers["User-Agent"] = str(user_agent)
                else:
                    signature = generated
            if signature:
                headers["Cipher-Text"] = str(signature)
        if self.before_request:
            self.before_request(path, params)
        return self.transport.get_json(self.base_url + path, params, headers)

    def _post(self, path: str, body: Mapping[str, Any]) -> Dict[str, Any]:
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json; charset=UTF-8"
        headers["Origin"] = self.base_url
        post_json = getattr(self.transport, "post_json", None)
        if not callable(post_json):
            raise NetworkError("当前传输层缺少 JSON POST 支持")
        if self.before_request:
            self.before_request(path, body)
        return post_json(self.base_url + path, body, headers)

    @staticmethod
    def _check_status(response: Mapping[str, Any]) -> None:
        status = response.get("status")
        message = str(response.get("message") or "")
        if status in (0, "0"):
            return
        if "request block" in " ".join(message.lower().split()):
            raise RequestBlockedError("请求被服务端拦截（%s: %s）" % (status, message))
        if status in STATUS_NOT_LOGGED_IN or "未登录" in message or "no login" in message.lower():
            raise AuthenticationError("Cookie 已失效或账号未登录")
        if status in STATUS_RATE_LIMIT or any(
            marker in message for marker in ("频率", "过于频繁", "异常访问", "访问受限")
        ) or "speed limit" in message.lower():
            raise RateLimitError("请求频率受限，请稍后重试")
        if status in STATUS_BAD_REQUEST or "bad request" in message.lower():
            raise BadRequestError("请求参数错误: %s" % (message or status))
        if status in (1, "1") and "no data" in message.lower():
            raise NoDataError("该条件没有指数数据")
        raise ApiError("接口状态 %r: %s" % (status, message or "未知错误"))

    def _ptbk(self, uniqid: str) -> str:
        last_error: Optional[Exception] = None
        for path in self.PTBK_PATHS:
            try:
                response = self._get(path, {"uniqid": uniqid})
                self._check_status(response)
                value = response.get("data")
                if isinstance(value, str) and value:
                    return value
                last_error = ResponseFormatError("ptbk 返回值为空")
            except (AuthenticationError, RateLimitError, RequestBlockedError):
                raise
            except (ApiError, HttpError, NetworkError) as exc:
                last_error = exc
        raise ResponseFormatError("获取 ptbk 失败: %s" % last_error)

    def query_keyword(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        area: Any = "全国",
    ) -> List[IndexPoint]:
        keyword = (keyword or "").strip()
        if not keyword:
            raise ValueError("关键词为空")
        validate_date_range(start_date, end_date)
        region = resolve_region(area)
        ranges = list(_daily_ranges(start_date, end_date))
        if len(ranges) > 1:
            rows: List[IndexPoint] = []
            for first, last in ranges:
                try:
                    rows.extend(self.query_keyword(keyword, first, last, region))
                except NoDataError:
                    # A missing window is not a reason to discard earlier
                    # windows or omit later dates from a long-range query.
                    continue
                except Exception as exc:
                    exc.partial_rows = rows + getattr(exc, "partial_rows", [])
                    raise
            if not rows:
                raise NoDataError("该条件没有指数数据")
            return rows
        params = {
            "area": str(region.area_id),
            "word": json.dumps([[{"name": keyword, "wordType": 1}]], ensure_ascii=False, separators=(",", ":")),
            "startDate": start_date,
            "endDate": end_date,
        }
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                response = self._get(self.SEARCH_PATH, params)
                self._check_status(response)
                data = response.get("data")
                if not isinstance(data, dict):
                    raise ResponseFormatError("SearchApi 返回数据为空")
                uniqid = str(data.get("uniqid") or response.get("uniqid") or "")
                if not uniqid:
                    raise ResponseFormatError("SearchApi 返回数据缺少 uniqid")
                ptbk = self._ptbk(uniqid)
                rows = parse_search_payload(response, ptbk, keyword, region)
                if not rows:
                    raise NoDataError("该条件没有指数数据")
                return rows
            except NoDataError:
                if self.on_no_data:
                    self.on_no_data("search", keyword, region, start_date, end_date)
                raise
            except (AuthenticationError, RateLimitError):
                raise
            except (NetworkError, HttpError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_delay * (2 ** attempt))
        raise ApiError(str(last_error or "查询失败"))

    def query_news_keyword(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        area: Any = "全国",
    ) -> List[IndexPoint]:
        keyword = (keyword or "").strip()
        if not keyword:
            raise ValueError("关键词为空")
        validate_date_range(start_date, end_date)
        region = resolve_region(area)
        ranges = list(_daily_ranges(start_date, end_date))
        if len(ranges) > 1:
            rows: List[IndexPoint] = []
            for first, last in ranges:
                try:
                    rows.extend(self.query_news_keyword(keyword, first, last, region))
                except NoDataError:
                    continue
                except Exception as exc:
                    exc.partial_rows = rows + getattr(exc, "partial_rows", [])
                    raise
            if not rows:
                raise NoDataError("该条件没有资讯指数数据")
            return rows
        params = {
            "area": str(region.area_id),
            "word": json.dumps([[{"name": keyword, "wordType": 1}]], ensure_ascii=False, separators=(",", ":")),
            "startDate": start_date,
            "endDate": end_date,
        }
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                response = self._get(self.NEWS_PATH, params)
                self._check_status(response)
                data = response.get("data")
                if not isinstance(data, dict):
                    raise ResponseFormatError("FeedSearchApi 返回数据为空")
                uniqid = str(data.get("uniqid") or response.get("uniqid") or "")
                if not uniqid:
                    raise ResponseFormatError("FeedSearchApi 返回数据缺少 uniqid")
                rows = parse_news_payload(response, self._ptbk(uniqid), keyword, region)
                if not rows:
                    raise NoDataError("该条件没有资讯指数数据")
                return rows
            except NoDataError:
                if self.on_no_data:
                    self.on_no_data("news", keyword, region, start_date, end_date)
                raise
            except (AuthenticationError, RateLimitError):
                raise
            except (NetworkError, HttpError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_delay * (2 ** attempt))
        raise ApiError(str(last_error or "资讯指数查询失败"))

    def query_keywords(
        self,
        keywords: Sequence[str],
        start_date: str,
        end_date: str,
        area: Any = "全国",
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[IndexPoint]:
        cleaned = [x.strip() for x in keywords if x and x.strip()]
        if not cleaned:
            raise ValueError("关键词列表为空")
        rows: List[IndexPoint] = []
        total = len(cleaned)
        for index, keyword in enumerate(cleaned, 1):
            if progress:
                progress(index - 1, total, keyword)
            rows.extend(self.query_keyword(keyword, start_date, end_date, area))
            if progress:
                progress(index, total, keyword)
        return rows

    def resolve_brand(self, keyword: str) -> BrandEntity:
        keyword = (keyword or "").strip()
        if not keyword:
            raise ValueError("品牌名称为空")
        response = self._post(self.BRAND_SUG_PATH, {"words": [keyword], "source": "pc_home"})
        self._check_status(response)
        data = response.get("data")
        results = data.get("result") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ResponseFormatError("品牌匹配结果格式异常")
        matches = []
        for item in results:
            info = item.get("brandInfo") if isinstance(item, dict) else None
            if not isinstance(info, dict) or not info.get("id"):
                continue
            name = str(item.get("word") or keyword)
            exact = 0 if name.strip().lower() == keyword.lower() else 1
            matches.append((exact, BrandEntity(
                name=name,
                entity_id=str(info["id"]),
                industry=str(info.get("trade2Name") or info.get("trade1Name") or ""),
                dimension={"brand": "品牌", "product": "产品", "firm": "厂商"}.get(
                    str(info.get("type") or item.get("type") or "brand"), "品牌"
                ),
            )))
        if not matches:
            raise NoDataError("未匹配到品牌：%s" % keyword)
        matches.sort(key=lambda value: value[0])
        return matches[0][1]

    def query_brand_metric(
        self,
        entity: BrandEntity,
        metric: str,
        start_date: str,
        end_date: str,
        area: Any = "全国",
    ) -> List[BrandIndexPoint]:
        if metric not in self.BRAND_PATHS:
            raise ValueError("未知品牌指数类型: %s" % metric)
        validate_date_range(start_date, end_date)
        region = resolve_region(area)
        body: Dict[str, Any] = {
            "entityId": entity.entity_id,
            "regionId": brand_area_id(region),
            "stat": True,
            "startDate": start_date,
            "endDate": end_date,
        }
        if metric == "brand_search":
            body["source"] = 0
        response = self._post(self.BRAND_PATHS[metric], body)
        self._check_status(response)
        values = response.get("data")
        if not isinstance(values, list):
            raise ResponseFormatError("品牌指数返回数据格式异常")
        province, city = region.csv_location
        rows: List[BrandIndexPoint] = []
        for item in values:
            if not isinstance(item, dict) or not item.get("statDate"):
                continue
            value = item.get("value")
            values_by_metric = {
                "brand_index": None,
                "brand_search": None,
                "brand_news": None,
                "brand_interaction": None,
            }
            values_by_metric[metric] = value
            rows.append(BrandIndexPoint(
                entity.name, str(item["statDate"]), province, city,
                values_by_metric["brand_index"], values_by_metric["brand_search"],
                values_by_metric["brand_news"], values_by_metric["brand_interaction"],
            ))
        if not rows:
            raise NoDataError("该条件没有品牌指数数据")
        return rows
