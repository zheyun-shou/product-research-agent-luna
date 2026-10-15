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


ORDER = """
Return the kept numbers in order: first the items whose title best matches the needs (the sizes, the colour, the
type and the wishes that the title shows), last the items whose title says nothing about them."""


class Keep(BaseModel):
    keep: list[int]


def screen_titles(cands: list[T], profile: CategoryProfile, llm, wanted: str | None = None,
                  needs: str | None = None) -> list[T]:
    """needs: the needs as text (conditions, budget, wishes). With it, the worker also orders the kept items, the
    best match first, so that the product pages of the best ones are read first."""
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {wanted or profile.display_name}"
    if needs:
        user += f"\nThe needs: {needs}"
    user += f"\n\nResults:\n{lines}\n\nReturn the numbers to keep" + (", the best match first." if needs else ".")
    keep = llm.structured("worker", SYSTEM + (ORDER if needs else ""), user, Keep).keep
    if not needs:
        return [c for n, c in enumerate(cands) if n in set(keep)]
    return [cands[n] for n in dict.fromkeys(keep) if 0 <= n < len(cands)]
