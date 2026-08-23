"""Constants recovered from the original x86 executable.

This module is the source-level contract used by the rewrite.  Addresses are
virtual addresses in the preserved PE image whose SHA-256 is recorded below;
they make each reconstructed behavior traceable to the binary rather than to
the screenshot alone.
"""

from __future__ import annotations

import datetime as dt

ORIGINAL_SHA256 = "414315adf6387fbea1f521c999028f7a33f4a10cba0c93a3a95ab2bc9e4a38ca"

# Original function 0x00405dde performs the input/date checks and references
# these literal dates at VA 0x005cdbe7 and 0x005cdc22.
SEARCH_EARLIEST = dt.date(2006, 6, 1)
NEWS_EARLIEST = dt.date(2017, 7, 3)
MAX_KEYWORDS = 3000

# Original modern request constructor: function 0x0042573a, template string at
# VA 0x005df2db.  The legacy/compatibility constructor is 0x00407687.
SEARCH_PATH = "/api/SearchApi/index"
NEWS_PATH = "/api/FeedSearchApi/getFeedIndex"
BRAND_SUG_PATH = "/insight/word/sug"
BRAND_PATHS = {
    "brand_index": "/insight/brand/queryBrandIndex",
    "brand_search": "/insight/brand/queryBrandSearchIndex",
    "brand_news": "/insight/brand/queryBrandFeedIndex",
    "brand_interaction": "/insight/brand/queryBrandClickIndex",
}
PTBK_PATHS = ("/Interface/api/ptbk", "/Interface/ptbk")
LOGIN_URL = "https://index.baidu.com/v2/index.html#/"
REFERER = "https://index.baidu.com/v2/main/index.html"

# Original ptbk request/decode routine: function 0x0041934f, string at
# VA 0x005d1221.  Status fixtures appear at 0x005d10f3..0x005d11af.
STATUS_NOT_LOGGED_IN = {10000, "10000"}
STATUS_RATE_LIMIT = {10001, "10001", 10018, "10018"}
STATUS_BAD_REQUEST = {10002, "10002"}

# CSV literals referenced by functions 0x00405dde and 0x00439a03.
SEARCH_HEADERS = ["关键词", "日期", "省份", "城市", "PC+移动端", "PC端", "移动端"]
NEWS_HEADERS = ["关键词", "日期", "省份", "城市", "资讯指数"]
BRAND_HEADERS = ["关键词", "日期", "省份", "城市", "品牌指数", "品牌搜索指数", "品牌资讯指数", "品牌互动指数"]

PERIOD_LABELS = {
    "daily": "天（日级）",
    "weekly": "周（日均）",
    "monthly": "月（日均）",
    "quarterly": "季（日均）",
    "yearly": "年（日均）",
    "overall": "总均值（日均）",
}

EVIDENCE = {
    "validation": "fcn.00405dde",
    "search_request": "fcn.0042573a / VA 0x005df2db",
    "ptbk_decode": "fcn.0041934f / VA 0x005d1221",
    "search_export": "fcn.00439a03 / VA 0x005cddf6",
    "login_webview": "VA 0x005e1b5b; wke cookie API table at 0x0067ff8c",
    "region_table": "read-only data around VA 0x005ce934",
}
