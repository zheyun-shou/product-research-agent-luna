"""Keep only search results of the target category, judged from the titles in one worker call."""
from typing import Protocol, TypeVar

from pydantic import BaseModel

from scout.profiles import CategoryProfile


class Titled(Protocol):
    title: str


T = TypeVar("T", bound=Titled)  # search candidates in crawl, stored cards in picks

SYSTEM = """You sort search results from amazon.se for a shopper.
Keep only items that are themselves a product of the category the shopper wants.
Drop every other item: accessories, spare parts, covers, cleaning products,
and products of other categories that the search also returned.
When unsure, keep the item."""


class Keep(BaseModel):
    keep: list[int]


def screen_titles(cands: list[T], profile: CategoryProfile, llm, wanted: str | None = None) -> list[T]:
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {wanted or profile.display_name}\n\nResults:\n{lines}\n\nReturn the numbers to keep."
    keep = set(llm.structured("worker", SYSTEM, user, Keep).keep)
    return [c for n, c in enumerate(cands) if n in keep]
