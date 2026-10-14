"""Rank the candidates by the rating and by the shopper's soft wishes (phase 3 Task 12).

For each wish, retrieval gives the chunks of each candidate that are nearest to the wish
(scout.text.retrieve.wish_matches). Near only means the same topic: "very loud" is near "quiet". So the worker
model reads these chunks and judges if they show that the candidate meets the wish, shows the opposite, or does not
say. The final score adds the scaled rating score and the mean wish score with fixed weights. Price is never in
the score."""
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field

from scout.picks.rank import Card
from scout.text.retrieve import Hit

RATING_WEIGHT = 0.5
WISHES_WEIGHT = 0.5  # shared by the wishes
LABEL_SCORE = {"meets": 1.0, "unclear": 0.5, "against": 0.0}
MAX_JUDGED = 30  # candidates judged in one call of find_products, the first by the rating
BATCH = 15  # candidates in one worker call
PART_CHARS = 400

Label = Literal["meets", "against", "unclear"]

SYSTEM = """You judge if stored texts of products show that each product meets a shopper's wish. For each product
you get the parts of its stored text that are nearest to the wish: specifications, descriptions and buyer reviews,
in any language.
- meets: the parts show that the product has it, for example a specification or buyers say that it is quiet.
- against: the parts show the opposite, for example buyers say that it is very loud.
- unclear: the parts do not say, they are about something else, or the buyers disagree about equally.
Judge only from the parts, not from what you know about the product. A word that only looks like the wish in
another language does not count (the French "vite" is not the Swedish "vit")."""


class WishVerdict(BaseModel):
    id: int = Field(description="The product id")
    verdict: Label
    part: int = Field(0, description="The number of the part that shows it best, or 0 when unclear")


class WishVerdicts(BaseModel):
    verdicts: list[WishVerdict]


@dataclass
class Verdict:
    label: Label
    hit: Hit | None = None  # the part that shows it


@dataclass
class Ranked:
    card: Card
    score: float
    verdicts: dict[str, Verdict] = field(default_factory=dict)  # wish -> verdict

    def moved_by(self) -> list[str]:
        return [w for w, v in self.verdicts.items() if v.label == "meets"]

    def against(self) -> list[str]:
        return [w for w, v in self.verdicts.items() if v.label == "against"]


def judge_wish(llm, wish: str, items: dict[int, tuple[str, list[Hit]]]) -> dict[int, Verdict]:
    """items: listing id -> (title, nearest chunks). One worker call for each BATCH products. A product that the
    model leaves out is unclear."""
    out: dict[int, Verdict] = {}
    ids = list(items)
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        blocks = []
        for i in batch:
            title, hits = items[i]
            parts = "\n".join(f"[{n}] ({h.kind}) {h.text[:PART_CHARS]}" for n, h in enumerate(hits, 1))
            blocks.append(f"Product id {i}: {title[:150]}\n{parts}")
        result = llm.structured("worker", SYSTEM, f"The wish: {wish}\n\n" + "\n\n".join(blocks), WishVerdicts)
        for v in result.verdicts:
            if v.id in batch:
                hits = items[v.id][1]
                out[v.id] = Verdict(v.verdict, hits[v.part - 1] if 1 <= v.part <= len(hits) else None)
    return {i: out.get(i, Verdict("unclear")) for i in ids}


def judge_wishes(llm, cards: list[Card], wishes: list[str],
                 matches: Callable[[list[int], list[str]], dict[int, list[list[Hit]]]],
                 cache: dict[tuple, Verdict]) -> dict[int, dict[str, Verdict]]:
    """The verdicts of the first MAX_JUDGED cards for each wish. The cache key is the wish and the ids of the chunks
    that the worker reads, so the same text is judged one time in a session, and new text (new reviews) is judged
    again. A card without indexed text is unclear."""
    cards = cards[:MAX_JUDGED]
    found = matches([c.listing_id for c in cards], wishes)
    keys = {(w, c.listing_id): (w, tuple(h.chunk_id for h in found[c.listing_id][i]))
            for c in cards if found.get(c.listing_id) for i, w in enumerate(wishes) if found[c.listing_id][i]}
    for i, wish in enumerate(wishes):
        items = {c.listing_id: (c.title, found[c.listing_id][i]) for c in cards
                 if (wish, c.listing_id) in keys and keys[(wish, c.listing_id)] not in cache}
        if items:
            cache.update({keys[(wish, k)]: v for k, v in judge_wish(llm, wish, items).items()})
    return {c.listing_id: {w: cache.get(keys.get((w, c.listing_id)), Verdict("unclear")) for w in wishes}
            for c in cards}


def _scaled(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    return [0.5 if high == low else (v - low) / (high - low) for v in values]


def rank_by_wishes(cards: list[Card], wishes: list[str], verdicts: dict[int, dict[str, Verdict]]) -> list[Ranked]:
    """The cards in the order of the final score. Without wishes, the order of rank() stays. The candidates with
    missing values come after the others, as in rank(). The cards that were not judged (after the first
    MAX_JUDGED) come after the judged ones, in the order of rank()."""
    if not cards:
        return []
    rating = _scaled([c.score for c in cards])
    if not wishes:
        return [Ranked(c, r) for c, r in zip(cards, rating)]
    ranked = []
    for n, card in enumerate(cards):
        mine = {w: (verdicts.get(card.listing_id) or {}).get(w, Verdict("unclear")) for w in wishes}
        wish_score = sum(LABEL_SCORE[v.label] for v in mine.values()) / len(wishes)
        ranked.append(Ranked(card, RATING_WEIGHT * rating[n] + WISHES_WEIGHT * wish_score, mine))
    judged = [r for r in ranked if r.card.listing_id in verdicts]
    rest = [r for r in ranked if r.card.listing_id not in verdicts]
    return sorted(judged, key=lambda r: (bool(r.card.missing), -r.score)) + rest
