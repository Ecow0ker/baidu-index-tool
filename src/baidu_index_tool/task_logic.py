"""Task validation and output naming reconstructed from the original flow."""

from __future__ import annotations

import datetime as dt
import pathlib
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .models import Region
from .reverse_contract import MAX_KEYWORDS, NEWS_EARLIEST, PERIOD_LABELS, SEARCH_EARLIEST


@dataclass(frozen=True)
class TaskSummary:
    keyword_count: int
    region_count: int
    request_count: int
    start_date: str
    end_date: str
    period: str


def validate_task(
    keywords: Sequence[str],
    regions: Sequence[Region],
    start_date: str,
    end_date: str,
    *,
    has_cookie: bool,
    has_channel: bool,
    include_news: bool = False,
) -> TaskSummary:
    """Mirror the original start-button guard sequence (fcn.00405dde)."""
    if not keywords:
        raise ValueError("请添加关键词内容")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError("关键词数量超过 %d 个" % MAX_KEYWORDS)
    if not regions:
        raise ValueError("请选择要查询的地区")
    if not has_cookie:
        raise ValueError("请先在软件内登录账号")
    if not has_channel:
        raise ValueError("请至少选择一个指数类型")
    try:
        start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("查询日期格式应为 YYYY-MM-DD") from exc
    if start < SEARCH_EARLIEST:
        raise ValueError("搜索指数最早日期为 %s" % SEARCH_EARLIEST.isoformat())
    if include_news and start < NEWS_EARLIEST:
        raise ValueError("资讯指数最早日期为 %s" % NEWS_EARLIEST.isoformat())
    if end >= dt.date.today():
        raise ValueError("结束日期最晚为昨天")
    if start > end:
        raise ValueError("查询日期范围设置不正确")
    return TaskSummary(len(keywords), len(regions), len(keywords) * len(regions), start_date, end_date, "")


def create_run_directory(root: pathlib.Path, now: Optional[dt.datetime] = None) -> Tuple[pathlib.Path, str]:
    now = now or dt.datetime.now()
    stamp = now.strftime("%Y年%m月%d日 %H时%M分%S秒")
    parent = pathlib.Path(root).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        path = parent / (stamp if index == 1 else "%s-%d" % (stamp, index))
        try:
            path.mkdir()
            return path, stamp
        except FileExistsError:
            index += 1


def output_filename(
    mode: str,
    stamp: str,
    period: str,
    start_date: str,
    end_date: str,
    *,
    keyword: str = "",
    province: str = "",
    city: str = "",
    kind: str = "search",
) -> str:
    label = PERIOD_LABELS[period]
    date_range = "%s-%s" % (start_date.replace("-", ""), end_date.replace("-", ""))
    suffix = "资讯指数" if kind == "news" else ("品牌指数" if kind == "brand" else label)
    if mode == "all":
        return "%s-%s.csv" % (stamp, suffix)
    components = [keyword]
    if mode == "keyword_area":
        components.extend(x for x in (province, city) if x and x != province)
        if province:
            components.insert(1, province)
    components.extend((suffix, date_range))
    return "-".join(x for x in components if x) + ".csv"
