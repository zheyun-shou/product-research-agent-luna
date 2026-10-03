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
    problem: Literal["captcha", "signin", "blocked", "gone"] | None = None
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


class ProductRecord(BaseModel):
    CORE_FIELDS: ClassVar[tuple[str, ...]] = ("title", "brand", "model", "price", "in_stock")

    source: str
    source_id: str
    url: str
    fetched_at: datetime
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    in_stock: bool | None = None
    variant_label: str = ""
    specs: dict[str, str] = Field(default_factory=dict)
    bullets: list[str] = Field(default_factory=list)
    text: str = ""
    attributes: dict[str, dict] = Field(default_factory=dict)
    llm_fields: list[str] = Field(default_factory=list)

    def missing_core(self) -> list[str]:
        return [name for name in self.CORE_FIELDS if getattr(self, name) is None]
