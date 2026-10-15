"""The search evaluation (phase 5 Task 7): where is the hidden product in the read order of a shop search?

It runs the search of each target with fixed words and needs, so it calls no model and costs nothing but the page
loads. It compares the search of 2026-10-14 (page 1 of the default, rating and sales orders, the budget only) with
the new one (the filters of the shop, the Threshold Algorithm, and Python's check of the card values). The worker
order of the title screen is not in it, because it needs a model."""
from decimal import Decimal
from typing import Callable

from scout.eval.hidden import Target
from scout.needs import Needs
from scout.profiles import CategoryProfile
from scout.search.filters import number_filters, off_condition, word_filters
from scout.search.threshold import Page, ResultList, threshold_search
from scout.sources.base import FilterGroup

READ_PAGES = 8  # the product pages of one search_shop call
OLD, NEW = "before", "with filters"


def place_of(target: Target, order: list) -> int | None:
    ids = [c.source_id for c in order]
    return next((n for n, i in enumerate(ids, 1) if i in target.ids()), None)


def kept_cards(order: list, needs: Needs, profile: CategoryProfile, check_cards: bool) -> list:
    budget = needs.budget_sek()
    return [c for c in order if (budget is None or c.price is None or c.price <= budget)
            and not (check_cards and off_condition(c, needs, profile))]


def search_target(target: Target, profile: CategoryProfile, fetch: Callable[[str], Page | None], adapter,
                  groups: Callable[[], list[FilterGroup]], list_pages: int, k: int = 10) -> dict:
    """fetch: a search URL gives its page, or None. groups: the filter groups that the first page stored."""
    case = target.search
    needs = Needs(category=profile.category, kind=target.kind, budget=case.budget, conditions=case.conditions)
    sorts = (None, *adapter.sorts)

    def lists(chosen: list[str]) -> list[ResultList]:
        made = [ResultList(f"{s or 'default'}{' with filters' if chosen else ''}",
                           lambda n, s=s: fetch(adapter.search_url(case.words, n, s, chosen))) for s in sorts]
        if chosen:
            made.append(ResultList("default", lambda n: fetch(adapter.search_url(case.words, n)), first=False))
        return made

    old = threshold_search(lists([]), k=k, max_pages=1)
    found = groups()
    words, unknown = word_filters(case.filters, found)
    chosen = adapter.price_filter(Decimal(str(case.budget)) if case.budget is not None else None) \
        + number_filters(needs, profile, found) + words
    new = threshold_search(lists(chosen), k=k, max_pages=list_pages)
    rows = {}
    for name, result, check in ((OLD, old, False), (NEW, new, True)):
        order = kept_cards(result.order, needs, profile, check)
        place = place_of(target, order)
        rows[name] = {"candidates": len(order), "place": place,
                      "calls": None if place is None else (place - 1) // READ_PAGES + 1,
                      "pages": result.pages, "stop": result.stop}
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
        for name in (OLD, NEW):
            row = r[name]
            where = f"place {row['place']} of {row['candidates']} (call {row['calls']})" if row["place"] \
                else f"not in the {row['candidates']} candidates"
            parts.append(f"{name}: {where}")
        lines.append(f"{r['target']}: " + "; ".join(parts))
    for name in (OLD, NEW):
        valid = [r for r in results if r.get("valid") is not False]
        within = sum(1 for r in valid if r[name]["calls"] == 1)
        lines.append(f"{name}: in the first {READ_PAGES} product pages for {within} of {len(valid)} targets")
    return "\n".join(lines)


def not_valid(target: Target, rec) -> str | None:
    """Why the target does not fit its own search now: the price is over the budget, it is out of stock, or a
    stored size is outside a condition. None when it fits."""
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
        if value > c.value if c.op == "<=" else value < c.value:
            return f"{c.attribute} is {value:g}, not {c.op} {c.value:g}"
    return None
