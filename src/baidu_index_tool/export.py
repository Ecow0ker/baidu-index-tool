from __future__ import annotations

import csv
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Iterable, List

from .models import BrandIndexPoint, IndexPoint
from .reverse_contract import BRAND_HEADERS, NEWS_HEADERS, SEARCH_HEADERS

HEADERS = SEARCH_HEADERS


@contextmanager
def _atomic_csv(output: pathlib.Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                         dir=output.parent, prefix="." + output.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary = pathlib.Path(handle.name)
            yield handle
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _format_number(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def write_csv(points: Iterable[IndexPoint], output: pathlib.Path) -> pathlib.Path:
    output = pathlib.Path(output).expanduser().resolve()
    with _atomic_csv(output) as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for row in points:
            writer.writerow([
                row.keyword, row.date, row.province, row.city,
                _format_number(row.overall), _format_number(row.pc), _format_number(row.mobile),
            ])
    return output


def write_news_csv(points: Iterable[IndexPoint], output: pathlib.Path) -> pathlib.Path:
    output = pathlib.Path(output).expanduser().resolve()
    with _atomic_csv(output) as handle:
        writer = csv.writer(handle)
        writer.writerow(NEWS_HEADERS)
        for row in points:
            writer.writerow([row.keyword, row.date, row.province, row.city, _format_number(row.news)])
    return output


def write_brand_csv(points: Iterable[BrandIndexPoint], output: pathlib.Path) -> pathlib.Path:
    output = pathlib.Path(output).expanduser().resolve()
    with _atomic_csv(output) as handle:
        writer = csv.writer(handle)
        writer.writerow(BRAND_HEADERS)
        for row in points:
            writer.writerow([
                row.keyword, row.date, row.province, row.city,
                _format_number(row.brand_index), _format_number(row.brand_search),
                _format_number(row.brand_news), _format_number(row.brand_interaction),
            ])
    return output


def read_csv(path: pathlib.Path) -> List[IndexPoint]:
    rows: List[IndexPoint] = []
    with pathlib.Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [x for x in HEADERS if x not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("CSV 缺少列: %s" % "、".join(missing))
        for item in reader:
            def number(name):
                value = (item.get(name) or "").strip()
                if value == "": return None
                try: return int(value)
                except ValueError: return float(value)
            rows.append(IndexPoint(
                item["关键词"], item["日期"], item["省份"], item["城市"],
                number("PC+移动端"), number("PC端"), number("移动端"),
            ))
    return rows
