from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import Iterable, List, Optional, Sequence

from .aggregate import METHODS, PERIODS
from .api import ApiError, AuthenticationError, RateLimitError
from .regions import find_regions
from .service import convert_files, default_output_path, load_cookie, query_and_export
from .transport import NetworkError


def _keywords(args: argparse.Namespace) -> List[str]:
    result = list(args.keyword or [])
    if args.keywords_file:
        text = pathlib.Path(args.keywords_file).read_text(encoding="utf-8-sig")
        result.extend(text.splitlines())
    cleaned = []
    seen = set()
    for item in result:
        item = item.strip()
        if item and item not in seen:
            cleaned.append(item)
            seen.add(item)
    if not cleaned:
        raise ValueError("至少提供一个 --keyword 或 --keywords-file")
    return cleaned


def _cookie(args: argparse.Namespace) -> str:
    if args.cookie_file:
        return load_cookie(pathlib.Path(args.cookie_file))
    value = os.environ.get("BAIDU_INDEX_COOKIE", "").strip()
    if not value:
        raise ValueError("请使用 --cookie-file，或设置 BAIDU_INDEX_COOKIE 环境变量")
    return value


def _input_csvs(values: Sequence[str]) -> List[pathlib.Path]:
    result: List[pathlib.Path] = []
    for value in values:
        path = pathlib.Path(value).expanduser()
        if path.is_dir():
            result.extend(sorted(path.rglob("*.csv")))
        elif path.is_file():
            result.append(path)
        else:
            raise ValueError("输入不存在: %s" % path)
    if not result:
        raise ValueError("没有找到 CSV 文件")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baidu-index", description="跨平台百度指数查询工具")
    sub = parser.add_subparsers(dest="command")

    query = sub.add_parser("query", help="查询搜索指数并导出 CSV")
    query.add_argument("-k", "--keyword", action="append", help="关键词；可重复")
    query.add_argument("--keywords-file", help="UTF-8 关键词文件，一行一个")
    query.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    query.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    query.add_argument("--area", default="全国", help="地区名称或 area ID，默认全国")
    query.add_argument("--cookie-file", help="Cookie header 文件或 Netscape cookies.txt")
    query.add_argument("-o", "--output", help="输出 CSV；默认写到当前目录 data/")
    query.add_argument("--aggregate", choices=PERIODS, default="daily", help="聚合周期")
    query.add_argument("--method", choices=METHODS, default="mean", help="聚合方法")
    query.add_argument("--timeout", type=float, default=20.0)
    query.add_argument("--retries", type=int, default=2)
    query.add_argument("--ca-file", help="自定义 CA 证书文件")

    convert = sub.add_parser("convert", help="转换已有日级 CSV")
    convert.add_argument("inputs", nargs="+", help="CSV 文件或目录")
    convert.add_argument("-o", "--output", required=True, help="输出 CSV")
    convert.add_argument("--aggregate", choices=PERIODS[1:], required=True)
    convert.add_argument("--method", choices=METHODS, default="mean")

    regions = sub.add_parser("regions", help="查询地区名称与 area ID")
    regions.add_argument("text", nargs="?", default="", help="名称、上级地区或 ID")

    sub.add_parser("gui", help="启动 Windows/macOS 图形界面")
    return parser


def _query(args: argparse.Namespace) -> int:
    words = _keywords(args)
    cookie = _cookie(args)
    output = pathlib.Path(args.output).expanduser() if args.output else default_output_path(pathlib.Path.cwd() / "data", args.aggregate)

    def progress(done: int, total: int, keyword: str) -> None:
        print("[%d/%d] %s" % (done, total, keyword), file=sys.stderr, flush=True)

    result = query_and_export(
        cookie, words, args.start, args.end, args.area, output,
        args.aggregate, args.method, progress, args.timeout, args.retries, args.ca_file,
    )
    print(str(result))
    return 0


def _convert(args: argparse.Namespace) -> int:
    paths = _input_csvs(args.inputs)
    result = convert_files(paths, pathlib.Path(args.output), args.aggregate, args.method)
    print(str(result))
    return 0


def _regions(args: argparse.Namespace) -> int:
    rows = find_regions(args.text)
    for region in rows:
        print("%4d\t%-8s\t%s" % (region.area_id, region.kind, region.display_name))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "query": return _query(args)
        if args.command == "convert": return _convert(args)
        if args.command == "regions": return _regions(args)
        if args.command == "gui":
            from .webview_gui import main as gui_main
            return gui_main([])
        parser.error("未知命令")
        return 2
    except AuthenticationError as exc:
        print("登录错误: %s" % exc, file=sys.stderr); return 3
    except RateLimitError as exc:
        print("频率限制: %s" % exc, file=sys.stderr); return 4
    except (ApiError, NetworkError) as exc:
        print("查询错误: %s" % exc, file=sys.stderr); return 5
    except (ValueError, OSError) as exc:
        print("参数/文件错误: %s" % exc, file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
