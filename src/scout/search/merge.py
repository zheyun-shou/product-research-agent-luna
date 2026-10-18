"""The read order of a shop search (2026-10-18): the result lists of the shop taken in turns.

The study of 2026-10-18 (the plan of phase 4) replayed all search runs. The default order of amazon, with only the
Python checks, was never clearly worse than the merges of several sort orders (RRF, the Threshold Algorithm), and it
loads far fewer pages. So a search reads the default order. A list with the number filters, or with the word filters
that the chat model gives, is taken in turns with it: the first card of each list, then the second of each."""
from scout.search.threshold import ResultList, ThresholdResult


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
    order, seen = [], set()
    for rank in range(max((len(rl.cards) for rl in lists), default=0)):
        for rl in lists:
            if rank < len(rl.cards) and rl.cards[rank].source_id not in seen:
                seen.add(rl.cards[rank].source_id)
                order.append(rl.cards[rank])
    return ThresholdResult(order=order, stop=stop, pages={rl.name: rl.pages for rl in lists},
                           failed=[rl.name for rl in lists if rl.failed], first=set(seen),
                           last_page=first_page + max((rl.pages for rl in lists), default=0) - 1)
