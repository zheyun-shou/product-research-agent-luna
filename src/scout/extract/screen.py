"""Keep only search results of the target category, judged from the titles in one worker call."""
from pydantic import BaseModel

from scout.profiles import CategoryProfile
from scout.sources.base import Candidate

SYSTEM = """You sort search results from amazon.se for a shopper.
Keep only items that are themselves a product of the category the shopper wants.
Drop every other item: accessories, spare parts, covers, cleaning products,
and products of other categories that the search also returned.
When unsure, keep the item."""


class Keep(BaseModel):
    keep: list[int]


def screen_titles(cands: list[Candidate], profile: CategoryProfile, llm) -> list[Candidate]:
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {profile.display_name}\n\nResults:\n{lines}\n\nReturn the numbers to keep."
    wanted = set(llm.structured("worker", SYSTEM, user, Keep).keep)
    return [c for n, c in enumerate(cands) if n in wanted]
