from __future__ import annotations

from typing import Iterable, List, Union

from .models import Region
from .region_data import REGION_ROWS

REGIONS = [Region(*row) for row in REGION_ROWS]


def all_regions() -> List[Region]:
    return list(REGIONS)


def is_city_region(region: Region) -> bool:
    # Municipalities have province-level API IDs, but belong in city selection.
    return region.kind == "city" or (
        region.kind == "province" and region.name in ("北京", "上海", "天津", "重庆")
    )


def find_regions(text: str) -> List[Region]:
    needle = (text or "").strip().lower()
    if not needle:
        return all_regions()
    return [r for r in REGIONS if needle in r.name.lower() or needle in r.parent.lower() or needle == str(r.area_id)]


def resolve_region(value: Union[str, int, Region]) -> Region:
    if isinstance(value, Region):
        return value
    text = str(value).strip()
    if text.isdigit():
        area_id = int(text)
        for region in REGIONS:
            if region.area_id == area_id:
                return region
        return Region("地区%s" % area_id, area_id, "", "custom")
    exact = [r for r in REGIONS if r.name == text or r.display_name == text]
    if exact:
        return exact[0]
    matches = find_regions(text)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("未知地区: %s；可使用 regions 子命令查询" % text)
    names = "、".join(r.display_name for r in matches[:8])
    raise ValueError("地区名称不唯一: %s（%s）" % (text, names))
