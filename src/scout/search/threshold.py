"""The Threshold Algorithm over the result lists of a shop (spec 4.3, phase 5 Task 6).

A shop gives its results in lists, for example its sort orders, with and without filters. Each list is read page by
page. The score of a card in a list falls with its place (1 / (60 + place), as in rank fusion). Only the place in the
lists that were read is known, so this is the variant without random access: a card has a lower bound (the sum of
its known scores) and an upper bound (plus the score of the last card read in each list where it is not seen yet).
The search stops when the k best lower bounds are at least as high as the threshold (the best score that a card not
seen yet can have) and as the upper bound of every other card, or at the page limit."""
from dataclasses import dataclass, field
from typing import Callable

from scout.sources.base import Candidate

PLACE_K = 60

Page = tuple[list[Candidate], bool]  # the cards of a page, and whether a next page exists


def place_score(place: int) -> float:
    """place counts from 1."""
    return 1 / (PLACE_K + place)


@dataclass
class ResultList:
    name: str
    fetch: Callable[[int], Page | None]  # a page number gives the page, or None when it did not load
    first: bool = True  # False: the cards that only this list has come after the others in the read order
    cards: list[Candidate] = field(default_factory=list)
    pages: int = 0
    done: bool = False
    failed: bool = False

    def last_score(self) -> float:
        """The best score in this list of a card not seen in it yet."""
        return 0.0 if self.done else place_score(len(self.cards) + 1)


@dataclass
class ThresholdResult:
    order: list[Candidate]  # the best first; cards of the lists marked first before the others
    stop: str  # why the search stopped
    pages: dict[str, int]  # the pages read in each list
    failed: list[str]  # the lists whose first page did not load


def _bounds(lists: list[ResultList]) -> tuple[dict[str, float], dict[str, float], dict[str, Candidate]]:
    lower: dict[str, float] = {}
    seen_in: dict[str, set[int]] = {}
    cards: dict[str, Candidate] = {}
    for n, rl in enumerate(lists):
        for place, card in enumerate(rl.cards, 1):
            if n in seen_in.setdefault(card.source_id, set()):
                continue  # the same card twice in one list counts once, at its first place
            seen_in[card.source_id].add(n)
            lower[card.source_id] = lower.get(card.source_id, 0.0) + place_score(place)
            cards.setdefault(card.source_id, card)
    upper = {i: lower[i] + sum(rl.last_score() for n, rl in enumerate(lists) if n not in seen_in[i]) for i in lower}
    return lower, upper, cards


def top_k_known(lists: list[ResultList], k: int) -> bool:
    lower, upper, _ = _bounds(lists)
    if len(lower) < k:
        return False
    best = sorted(lower, key=lower.get, reverse=True)
    kth = lower[best[k - 1]]
    threshold = sum(rl.last_score() for rl in lists)
    others = max((upper[i] for i in best[k:]), default=0.0)
    return kth >= threshold and kth >= others


def threshold_search(lists: list[ResultList], k: int = 10, max_pages: int = 2, first_page: int = 1) \
        -> ThresholdResult:
    """Read one more page of each list in each round, up to max_pages pages of each list."""
    stop = "the page limit"
    for number in range(first_page, first_page + max_pages):
        for rl in lists:
            if rl.done:
                continue
            got = rl.fetch(number)
            if got is None:
                rl.done = True
                rl.failed = rl.pages == 0
                continue
            found, has_next = got
            rl.cards += found
            rl.pages += 1
            rl.done = not has_next or not found
        if all(rl.done for rl in lists):
            stop = "all results read"
            break
        if top_k_known(lists, k):
            stop = f"the best {k} are known"
            break
    lower, _, cards = _bounds(lists)
    in_first = {c.source_id for rl in lists if rl.first for c in rl.cards}
    order = sorted(cards.values(), key=lambda c: (c.source_id not in in_first, -lower[c.source_id]))
    return ThresholdResult(order=order, stop=stop, pages={rl.name: rl.pages for rl in lists},
                           failed=[rl.name for rl in lists if rl.failed])
