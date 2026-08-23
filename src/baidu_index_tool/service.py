from __future__ import annotations

import datetime as dt
import pathlib
from typing import Callable, List, Optional, Sequence

from .aggregate import aggregate_points
from .api import BaiduIndexClient
from .export import read_csv, write_csv
from .models import IndexPoint


def load_cookie(path: pathlib.Path) -> str:
    path = pathlib.Path(path).expanduser()
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise ValueError("Cookie 文件为空: %s" % path)
    # Accept a plain Cookie header. For a Netscape file, combine non-comment rows.
    if "\t" in text and "\n" in text:
        pairs = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                pairs.append("%s=%s" % (fields[5], fields[6]))
        if pairs:
            return "; ".join(pairs)
    return text.splitlines()[0].strip()


def default_output_path(directory: pathlib.Path, period: str) -> pathlib.Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = {
        "daily": "日级", "weekly": "周(日均)", "monthly": "月(日均)",
        "quarterly": "季(日均)", "yearly": "年(日均)", "overall": "总均值",
    }[period]
    return pathlib.Path(directory) / ("百度指数-%s-%s.csv" % (stamp, suffix))


def query_and_export(
    cookie: str,
    keywords: Sequence[str],
    start_date: str,
    end_date: str,
    area: str,
    output: pathlib.Path,
    period: str = "daily",
    method: str = "mean",
    progress: Optional[Callable[[int, int, str], None]] = None,
    timeout: float = 20.0,
    retries: int = 2,
    ca_file: Optional[str] = None,
) -> pathlib.Path:
    client = BaiduIndexClient(cookie, timeout=timeout, retries=retries, ca_file=ca_file)
    rows = client.query_keywords(keywords, start_date, end_date, area, progress)
    return write_csv(aggregate_points(rows, period, method), output)


def convert_files(inputs: Sequence[pathlib.Path], output: pathlib.Path, period: str, method: str) -> pathlib.Path:
    rows: List[IndexPoint] = []
    for path in inputs:
        rows.extend(read_csv(path))
    return write_csv(aggregate_points(rows, period, method), output)
