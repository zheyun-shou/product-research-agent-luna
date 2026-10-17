"""SQL filter for budget and stock, Python conditions and a Bayesian rating score."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

import psycopg

from scout.sources.amazon_se.numbers import to_decimal

PRIOR_WEIGHT = 20  # reviews needed before a product's own rating counts as much as the average
SIZES = ("height", "width", "depth")

CARDS_SQL = """
SELECT l.id AS listing_id, p.id AS product_id, COALESCE(l.title, p.name) AS title, p.brand, p.model, l.url,
       v.price, v.currency, v.in_stock, v.label AS variant, l.rating, l.review_count, p.attributes, l.fetched_at
FROM listings l
JOIN products p ON p.id = l.product_id
-- The newest price and stock of the listing: the page itself, or the page of the size of the shopper (variants.py)
JOIN LATERAL (SELECT price, currency, in_stock, label FROM variants WHERE listing_id = l.id
              ORDER BY updated_at DESC LIMIT 1) v ON true
WHERE p.category = %(category)s
  AND v.in_stock IS DISTINCT FROM false
  AND (%(budget)s::numeric IS NULL OR (v.price IS NOT NULL AND v.price <= %(budget)s::numeric))
ORDER BY l.id
"""
_CONDITION = re.compile(r"^\s*(\w+)\s*(<=|>=)\s*([\d.,]+)\s*$")


@dataclass
class Card:
    listing_id: int
    product_id: int
    title: str
    brand: str | None
    model: str | None
    url: str
    price: Decimal | None
    currency: str | None
    in_stock: bool | None
    rating: Decimal | None
    review_count: int | None
    attributes: dict
    fetched_at: datetime
    score: float = 0.0
    missing: list[str] = field(default_factory=list)
    variant: str = ""  # the colour and the size of the price, e.g. "Core Black / 38 EU"


@dataclass(frozen=True)
class Condition:
    attribute: str
    op: Literal["<=", ">="]
    value: float


def parse_condition(text: str) -> Condition:
    match = _CONDITION.match(text)
    number = to_decimal(match.group(3)) if match else None
    if not match or number is None:
        raise ValueError(f"Cannot read '{text}'. Write it like height<=180 or total_volume>=300.")
    return Condition(match.group(1), match.group(2), float(number))


def load_cards(conn: psycopg.Connection, category: str, budget: Decimal | None) -> list[Card]:
    rows = conn.execute(CARDS_SQL, {"category": category, "budget": budget}).fetchall()
    return [Card(**row) for row in rows]


def apply_conditions(cards: list[Card], conditions: list[Condition]) -> tuple[list[Card], int]:
    kept, dropped = [], 0
    for card in cards:
        missing, passes = [], True
        for cond in conditions:
            entry = card.attributes.get(cond.attribute) or {}
            if "values" in entry:  # variants, for example the sizes: one of them must be inside the condition
                if not any(v <= cond.value if cond.op == "<=" else v >= cond.value for v in entry["values"]):
                    passes = False
                    break
                continue
            if entry.get("value") is None:
                missing.append(cond.attribute)
                continue
            value = float(entry["value"])
            if (cond.op == "<=" and value > cond.value) or (cond.op == ">=" and value < cond.value):
                passes = False
                break
        if passes:
            card.missing = missing
            kept.append(card)
        else:
            dropped += 1
    return kept, dropped


def bayesian(rating: Decimal | None, count: int | None, prior_mean: float, weight: int = PRIOR_WEIGHT) -> float:
    if rating is None or not count:
        return prior_mean
    return (count * float(rating) + weight * prior_mean) / (count + weight)


def rank(cards: list[Card]) -> list[Card]:
    rated = [float(c.rating) for c in cards if c.rating is not None and c.review_count]
    prior = sum(rated) / len(rated) if rated else 0.0
    for card in cards:
        card.score = bayesian(card.rating, card.review_count, prior)
    ranked, seen = [], set()
    for card in sorted(cards, key=lambda c: (bool(c.missing), -c.score)):
        if card.product_id not in seen:  # colours of one product are separate listings
            seen.add(card.product_id)
            ranked.append(card)
    return ranked


CARDS_BY_ID_SQL = """
SELECT DISTINCT ON (l.id) l.id AS listing_id, p.id AS product_id, COALESCE(l.title, p.name) AS title, p.brand,
       p.model, l.url, v.price, v.currency, v.in_stock, v.label AS variant, l.rating, l.review_count, p.attributes,
       l.fetched_at
FROM listings l
JOIN products p ON p.id = l.product_id
JOIN variants v ON v.listing_id = l.id
WHERE l.id = ANY(%(ids)s)
ORDER BY l.id, v.updated_at DESC
"""


def load_cards_by_ids(conn: psycopg.Connection, listing_ids: list[int]) -> list[Card]:
    rows = conn.execute(CARDS_BY_ID_SQL, {"ids": list(listing_ids)}).fetchall()
    by_id = {row["listing_id"]: Card(**row) for row in rows}
    return [by_id[i] for i in listing_ids if i in by_id]
