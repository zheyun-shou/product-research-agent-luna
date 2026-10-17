"""The search evaluation (phase 5 Task 7): where is the hidden product in the read order of a shop search?

It runs the search of each target with fixed words and needs, so it calls no model and costs nothing but the page
loads. It compares three searches. "before" is the search of 2026-10-14: page 1 of the default, rating and sales
orders taken in turns, with the budget only. "without filters" is the same pages in the order of the Threshold
Algorithm. "with filters" is the new search: the filters of the shop, the Threshold Algorithm and Python's check of
the card values. The lists and the checks are the ones of the crawl. The worker order of the title screen is not in
it, because it needs a model."""
from decimal import Decimal
from typing import Callable

from scout.crawl import keep_cards, result_lists
from scout.eval.hidden import Target
from scout.needs import Needs
from scout.profiles import CategoryProfile
from scout.search.filters import number_filters, off_condition, outside, word_filters
from scout.search.threshold import Page, threshold_search
from scout.sources.base import FilterGroup

READ_PAGES = 8  # the product pages of one search_shop call
OLD, FUSED, NEW = "before", "without filters", "with filters"
ROWS = (OLD, FUSED, NEW)


def interleave(orders: list[list]) -> list:
    """The order of the crawl of 2026-10-14: the first of each order, then the second of each, without repeats."""
    merged, seen = [], set()
    for rank in range(max((len(o) for o in orders), default=0)):
        for order in orders:
            if rank < len(order) and order[rank].source_id not in seen:
                seen.add(order[rank].source_id)
                merged.append(order[rank])
    return merged


def place_of(target: Target, order: list) -> int | None:
    ids = [c.source_id for c in order]
    return next((n for n, i in enumerate(ids, 1) if i in target.ids()), None)


def search_target(target: Target, profile: CategoryProfile, fetch: Callable[[str], Page | None], adapter,
                  groups: Callable[[], list[FilterGroup]], list_pages: int, k: int = 10) -> dict:
    """fetch: a search URL gives its page, or None. groups: the filter groups that the first page stored."""
    case = target.search
    needs = Needs(category=profile.category, kind=target.kind, budget=case.budget, conditions=case.conditions)
    budget = needs.budget_sek()

    def fetcher(sort, chosen, name):
        return lambda n: fetch(adapter.search_url(case.words, n, sort, chosen))

    pages_1 = [fetcher(s, [], "")(1) for s in (None, *adapter.sorts)]
    old = interleave([p[0] for p in pages_1 if p])
    fused = threshold_search(result_lists(fetcher, adapter.sorts, []), k=k, max_pages=1)
    words, unknown = word_filters(case.filters, groups())
    chosen = adapter.price_filter(budget) + number_filters(needs, profile, groups()) + words
    new = threshold_search(result_lists(fetcher, adapter.sorts, chosen, words), k=k, max_pages=list_pages)
    rows = {}
    for name, order, result, check in ((OLD, old, None, None), (FUSED, fused.order, fused, None),
                                       (NEW, new.order, new, lambda c: off_condition(c, needs, profile))):
        kept = keep_cards(order, budget, check, {})
        place = place_of(target, kept)
        rows[name] = {"candidates": len(kept), "place": place,
                      "calls": None if place is None else (place - 1) // READ_PAGES + 1}
        if result is not None:
            rows[name] |= {"pages": result.pages, "stop": result.stop}
    rows[NEW]["filters"] = chosen
    rows[NEW]["unknown_filters"] = unknown
    return rows


def summary_text(results: list[dict]) -> str:
    lines = []
    for r in results:
        if r.get("valid") is False:
            lines.append(f"{r['target']}: not valid now ({r['why']})")
            continue
        parts = []
        for name in ROWS:
            row = r[name]
            where = f"place {row['place']} of {row['candidates']} (call {row['calls']})" if row["place"] \
                else f"not in the {row['candidates']} candidates"
            parts.append(f"{name}: {where}")
        lines.append(f"{r['target']}: " + "; ".join(parts))
    for name in ROWS:
        valid = [r for r in results if r.get("valid") is not False]
        within = sum(1 for r in valid if r[name]["calls"] == 1)
        lines.append(f"{name}: in the first {READ_PAGES} product pages for {within} of {len(valid)} targets")
    return "\n".join(lines)


def not_valid(target: Target, rec) -> str | None:
    """Why the target does not fit its own search now: the price is over the budget, it is out of stock, or a size
    on its page (the product dimensions that the selectors read, without a model) is outside a condition. A size
    that the page does not give is not checked. None when it fits."""
    case = target.search
    if rec.in_stock is False:
        return "out of stock"
    if case.budget is not None and rec.price is not None and rec.price > Decimal(str(case.budget)):
        return f"the price is {rec.price} SEK, over the budget of {case.budget:g} SEK"
    for c in case.conditions:
        entry = rec.attributes.get(c.attribute) or {}
        value = entry.get("value") if isinstance(entry, dict) else entry
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if outside(value, c):
            return f"{c.attribute} is {value:g}, not {c.op} {c.value:g}"
    return None
