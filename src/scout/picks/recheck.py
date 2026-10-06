"""Recheck every pick's price and stock right before showing it."""
from dataclasses import replace
from decimal import Decimal
from typing import Callable, Sequence

from scout.picks.rank import Card, Condition, apply_conditions
from scout.sources.base import ProductRecord


def recheck_picks(
    ranked: list[Card],
    k: int,
    refetch: Callable[[Card], ProductRecord | None],
    budget: Decimal | None,
    conditions: Sequence[Condition] = (),
) -> tuple[list[Card], list[str]]:
    picks, notes = [], []
    for card in ranked:
        if len(picks) == k:
            break
        rec = refetch(card)
        if rec is None:
            notes.append(f"{card.title} could not be rechecked, so it was left out.")
            continue
        if rec.in_stock is False:
            notes.append(f"{card.title} is sold out now and was replaced.")
            continue
        if budget is not None and rec.price is None:
            notes.append(f"{card.title} shows no price now, so the budget cannot be checked, and it was replaced.")
            continue
        if budget is not None and rec.price > budget:
            notes.append(f"{card.title} now costs {rec.price} {rec.currency}, over budget, and was replaced.")
            continue
        fresh = replace(card, price=rec.price, currency=rec.currency, in_stock=rec.in_stock,
                        fetched_at=rec.fetched_at, attributes={**card.attributes, **rec.attributes})
        # The page can show sizes that the stored card did not have, so check the conditions again.
        kept, failed = apply_conditions([fresh], list(conditions))
        if failed:
            wanted = ", ".join(f"{c.attribute}{c.op}{c.value:g}" for c in conditions)
            notes.append(f"{card.title} does not meet {wanted} with the sizes on the page now, and was replaced.")
            continue
        picks.append(kept[0])
    return picks, notes
