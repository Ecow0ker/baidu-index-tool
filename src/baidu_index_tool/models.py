from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

Number = Union[int, float]


@dataclass(frozen=True)
class Region:
    name: str
    area_id: int
    parent: str = ""
    kind: str = "province"

    @property
    def csv_location(self) -> Tuple[str, str]:
        if self.kind == "city":
            return self.parent, self.name
        return self.name, self.name

    @property
    def display_name(self) -> str:
        return "%s / %s" % (self.parent, self.name) if self.parent else self.name


@dataclass(frozen=True)
class IndexPoint:
    keyword: str
    date: str
    province: str
    city: str
    overall: Optional[Number]
    pc: Optional[Number]
    mobile: Optional[Number]
    news: Optional[Number] = None


@dataclass(frozen=True)
class BrandEntity:
    name: str
    entity_id: str
    industry: str = ""
    dimension: str = "品牌"


@dataclass(frozen=True)
class BrandIndexPoint:
    keyword: str
    date: str
    province: str
    city: str
    brand_index: Optional[Number] = None
    brand_search: Optional[Number] = None
    brand_news: Optional[Number] = None
    brand_interaction: Optional[Number] = None
