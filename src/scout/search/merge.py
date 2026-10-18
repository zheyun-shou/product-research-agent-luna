"""The read order of a shop search: the result lists of the shop taken in turns.

The study of 2026-10-18 (the plan of phase 4) replayed all search runs. The default order of amazon, with only the
Python checks, was never clearly worse than the merges of several sort orders (RRF, the Threshold Algorithm), and it
loads far fewer pages. On 2026-10-18 a search took the default order in turns with a list with the number filters
and a list with the word filters of the chat model. These lists were not clearly better either, so since 2026-10-19
the crawl reads the default order only. The evaluation still takes the lists of 2026-10-18 in turns, for the
comparison."""
from scout.search.threshold import ResultList, ThresholdResult
from scout.sources.base import Candidate


def interleave(lists: list[list[Candidate]]) -> list[Candidate]:
    """The first card of each list, then the second of each. A card counts once, at its first place."""
    merged, seen = [], set()
    for rank in range(max((len(cards) for cards in lists), default=0)):
        for cards in lists:
            if rank < len(cards) and cards[rank].source_id not in seen:
                seen.add(cards[rank].source_id)
                merged.append(cards[rank])
    return merged


def interleave_search(lists: list[ResultList], max_pages: int = 2, first_page: int = 1) -> ThresholdResult:
    """Read pages first_page to first_page + max_pages - 1 of each list, then take the lists in turns. A card counts
    once, at its first place."""
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
            break
    stop = "all results read" if all(rl.done for rl in lists) else "the page limit"
    return ThresholdResult(order=interleave([rl.cards for rl in lists]), stop=stop,
                           pages={rl.name: rl.pages for rl in lists}, failed=[rl.name for rl in lists if rl.failed],
                           first={c.source_id for rl in lists if rl.first for c in rl.cards},
                           last_page=first_page + max((rl.pages for rl in lists), default=0) - 1)
