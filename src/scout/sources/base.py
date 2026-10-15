"""Records passed between adapters, extraction and storage."""
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, Field


class RawPage(BaseModel):
    url: str
    final_url: str
    status: int
    html: str
    fetched_at: datetime
    duration_ms: int = 0
    snapshot_path: str | None = None


class SessionStatus(BaseModel):
    ok: bool
    problem: Literal["captcha", "signin", "blocked", "gone", "incomplete", "unavailable"] | None = None
    detail: str = ""


class Candidate(BaseModel):
    source: str
    source_id: str
    url: str
    title: str
    price: Decimal | None = None
    currency: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    sponsored: bool = False


class FilterOption(BaseModel):
    """One option of a filter group of a search page. low and high: the range of a number option, in unit."""
    name: str
    value: str  # the key of the option in the URL of the shop
    low: float | None = None
    high: float | None = None
    unit: str | None = None


class FilterGroup(BaseModel):
    key: str
    name: str
    options: list[FilterOption]

    @property
    def numeric(self) -> bool:
        return bool(self.options) and all(o.low is not None or o.high is not None for o in self.options)


class ProductRecord(BaseModel):
    CORE_FIELDS: ClassVar[tuple[str, ...]] = ("title", "brand", "model", "price", "in_stock")

    source: str
    source_id: str
    url: str
    fetched_at: datetime
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    gtin: str | None = None  # 14 digits; EAN-13 and UPC-A get leading zeros
    price: Decimal | None = None
    currency: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    in_stock: bool | None = None
    variant_label: str = ""
    specs: dict[str, str] = Field(default_factory=dict)
    bullets: list[str] = Field(default_factory=list)
    text: str = ""
    description: str = ""  # the product description and the A+ text of the seller
    reviews: list[dict] = Field(default_factory=list)  # title, rating, date, verified, text; never the author
    images: list[str] = Field(default_factory=list)  # image URLs: the product images, then the A+ images
    attributes: dict[str, dict] = Field(default_factory=dict)
    llm_fields: list[str] = Field(default_factory=list)

    def missing_core(self) -> list[str]:
        return [name for name in self.CORE_FIELDS if getattr(self, name) is None]
