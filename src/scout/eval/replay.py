"""Offline replay of the search evaluation (2026-10-18). Each run of `scout eval-search` saved its search pages as
snapshots. The replay reads the pages of one run again and orders them with several merge methods, so all methods
see the same pages and the changes of amazon between runs do not count. It loads no page and calls no model.

A list is one sort order of the shop with one set of filters: "none", "safe" (the price and number filters) or
"all" (also the word filters). Each method gives one read order. The place of the target is counted after the same
Python checks for all methods (the budget, and the card values against the conditions)."""
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from scout.crawl import keep_cards
from scout.eval.hidden import Target
from scout.eval.search import READ_PAGES, place_of
from scout.needs import Needs
from scout.profiles import CategoryProfile
from scout.search.filters import off_condition
from scout.search.merge import interleave
from scout.sources.amazon_se.search import has_next_page, parse_search_page
from scout.sources.amazon_se.urls import search_url, snapshot_key
from scout.sources.base import Candidate

SORTS = (None, "rating", "sales")
STAMP = "%Y%m%dT%H%M%S"


@dataclass(frozen=True)
class Run:
    """One eval-search run: the snapshots with a time stamp in [start, end] (UTC, as in the file names)."""
    name: str
    start: datetime
    end: datetime


def snapshot_html(snapshot_dir: Path, url: str, run: Run) -> str | None:
    folder = snapshot_dir / "amazon_se" / "search" / re.sub(r"[^\w.-]+", "_", snapshot_key(url)).strip("_")[:80]
    files = [f for f in folder.glob("*.html") if run.start <= datetime.strptime(f.stem, STAMP) <= run.end]
    return max(files).read_text(encoding="utf-8") if files else None


def run_lists(target: Target, filters: dict[str, list[str]], snapshot_dir: Path, run: Run, pages: int = 2) \
        -> dict[tuple[str | None, str], list[list[Candidate]]]:
    """The pages of each list of the run that has snapshots, keyed by (sort, filter set). filters: the filter set
    name gives its option values. Pages 1 to pages, as far as they exist."""
    lists = {}
    for kind, chosen in filters.items():
        if kind != "none" and not chosen:
            continue
        for sort in SORTS:
            found = []
            for number in range(1, pages + 1):
                html = snapshot_html(snapshot_dir, search_url(target.search.words, number, sort, chosen), run)
                if html is None:
                    break
                found.append(parse_search_page(html))
                if not has_next_page(html):
                    break
            if found:
                lists[(sort, kind)] = found
    return lists


def cards_of(pages: list[list[Candidate]], count: int | None = None) -> list[Candidate]:
    """The cards of the first count pages (all pages when None); a card counts once, at its first place."""
    out, seen = [], set()
    for page in pages[:count]:
        for c in page:
            if c.source_id not in seen:
                seen.add(c.source_id)
                out.append(c)
    return out


def rrf(lists: list[list[Candidate]], k: int = 60) -> list[Candidate]:
    """Reciprocal rank fusion (Cormack, Clarke and Buettcher 2009): the sum of 1 / (k + place)."""
    score: dict[str, float] = {}
    cards: dict[str, Candidate] = {}
    for one in lists:
        for place, c in enumerate(one, 1):
            score[c.source_id] = score.get(c.source_id, 0.0) + 1 / (k + place)
            cards.setdefault(c.source_id, c)
    return sorted(cards.values(), key=lambda c: -score[c.source_id])


def tiers(first: list[Candidate], later: list[Candidate]) -> list[Candidate]:
    ids = {c.source_id for c in first}
    return first + [c for c in later if c.source_id not in ids]


def methods(lists: dict, numbers: bool = False) -> dict[str, list[Candidate]]:
    """The read order of each method. U: no filters, page 1 only (the search of 2026-10-14). F: the filtered lists
    (the sorts with the safe filters and the default order with all filters), pages 1 and 2. default: the default
    order without filters, pages 1 and 2. numbers: the safe filters have number filters, not only the price; then
    "10/18" (the search of the crawl on 2026-10-18) takes the default order with them in turns with the others.
    "amazon default" is the search of the crawl since 2026-10-19."""
    def get(keys, count=None):
        return [cards_of(lists[k], count) for k in keys if k in lists]
    u1 = get([(s, "none") for s in SORTS], 1)
    f = get([(s, "safe") for s in SORTS] + [(None, "all")])
    default = cards_of(lists.get((None, "none"), []))
    every = f + get([(s, "none") for s in SORTS])
    return {
        "amazon default": default,
        "interleave U": interleave(u1),
        "rrf U": rrf(u1),
        "rrf F, then default": tiers(rrf(f), default) if f else [],
        "interleave F, then default": tiers(interleave(f), default) if f else [],
        "rrf all": rrf(every),
        "interleave all": interleave(every),
        "10/18": interleave(([cards_of(lists[(None, "safe")])] if numbers and (None, "safe") in lists else [])
                            + get([(None, "all")]) + [default]),
    }


def score_methods(target: Target, profile: CategoryProfile, orders: dict[str, list[Candidate]],
                  rerank: Callable[[Target, list[Candidate]], list[Candidate]] | None = None) -> dict[str, dict]:
    case = target.search
    needs = Needs(category=profile.category, kind=target.kind, budget=case.budget, conditions=case.conditions)
    budget = needs.budget_sek()
    if rerank is not None and orders.get("interleave all"):
        orders = orders | {"rerank all": rerank(target, orders["interleave all"])}
    out = {}
    for name, order in orders.items():
        kept = keep_cards(order, budget, lambda c: off_condition(c, needs, profile), {})
        place = place_of(target, kept)
        out[name] = {"place": place, "candidates": len(kept), "hit": place is not None and place <= READ_PAGES,
                     "rr": 0.0 if place is None else 1 / place}
    return out


def list_places(target: Target, lists: dict) -> dict[str, int | None]:
    """The place of the target in each list, before any check: the raw material of the analysis."""
    return {f"{s or 'default'}/{kind}": place_of(target, cards_of(pages)) for (s, kind), pages in lists.items()}
