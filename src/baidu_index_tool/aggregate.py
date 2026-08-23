from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import BrandIndexPoint, IndexPoint, Number

PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly", "overall")
METHODS = ("mean", "sum")


def _parse_daily(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("聚合输入必须是日级 YYYY-MM-DD 日期，收到: %s" % value) from exc


def _period_key(date: dt.date, period: str) -> Tuple:
    if period == "weekly":
        year, week, _ = date.isocalendar()
        return year, week
    if period == "monthly":
        return date.year, date.month
    if period == "quarterly":
        return date.year, (date.month - 1) // 3 + 1
    if period == "yearly":
        return (date.year,)
    if period == "overall":
        return ("all",)
    return (date.isoformat(),)


def _aggregate(values: Sequence[Optional[Number]], method: str) -> Optional[Number]:
    usable = [Decimal(str(v)) for v in values if v is not None]
    if not usable:
        return None
    result = sum(usable) if method == "sum" else sum(usable) / Decimal(len(usable))
    rounded = result.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded)


def aggregate_points(points: Iterable[IndexPoint], period: str = "daily", method: str = "mean") -> List[IndexPoint]:
    if period not in PERIODS:
        raise ValueError("未知聚合周期: %s" % period)
    if method not in METHODS:
        raise ValueError("未知聚合方法: %s" % method)
    rows = list(points)
    if period == "daily":
        return sorted(rows, key=lambda x: (x.keyword, x.province, x.city, x.date))
    groups: Dict[Tuple, List[Tuple[dt.date, IndexPoint]]] = defaultdict(list)
    for row in rows:
        date = _parse_daily(row.date)
        key = (row.keyword, row.province, row.city, _period_key(date, period))
        groups[key].append((date, row))
    result: List[IndexPoint] = []
    for key, items in groups.items():
        items.sort(key=lambda x: x[0])
        start, end = items[0][0], items[-1][0]
        label = "%s-%s" % (start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        base = items[0][1]
        result.append(IndexPoint(
            base.keyword,
            label,
            base.province,
            base.city,
            _aggregate([x[1].overall for x in items], method),
            _aggregate([x[1].pc for x in items], method),
            _aggregate([x[1].mobile for x in items], method),
            _aggregate([x[1].news for x in items], method),
        ))
    return sorted(result, key=lambda x: (x.keyword, x.province, x.city, x.date))


def aggregate_brand_points(
    points: Iterable[BrandIndexPoint], period: str = "daily", method: str = "mean",
) -> List[BrandIndexPoint]:
    if period not in PERIODS:
        raise ValueError("未知聚合周期: %s" % period)
    if method not in METHODS:
        raise ValueError("未知聚合方法: %s" % method)
    rows = list(points)
    if period == "daily":
        return sorted(rows, key=lambda x: (x.keyword, x.province, x.city, x.date))
    groups: Dict[Tuple, List[Tuple[dt.date, BrandIndexPoint]]] = defaultdict(list)
    for row in rows:
        date = _parse_daily(row.date)
        key = (row.keyword, row.province, row.city, _period_key(date, period))
        groups[key].append((date, row))
    result: List[BrandIndexPoint] = []
    for items in groups.values():
        items.sort(key=lambda x: x[0])
        start, end = items[0][0], items[-1][0]
        base = items[0][1]
        result.append(BrandIndexPoint(
            base.keyword,
            "%s-%s" % (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
            base.province,
            base.city,
            _aggregate([x[1].brand_index for x in items], method),
            _aggregate([x[1].brand_search for x in items], method),
            _aggregate([x[1].brand_news for x in items], method),
            _aggregate([x[1].brand_interaction for x in items], method),
        ))
    return sorted(result, key=lambda x: (x.keyword, x.province, x.city, x.date))
