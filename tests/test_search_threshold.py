from scout.search.threshold import ResultList, place_score, threshold_search
from scout.sources.base import Candidate


def card(i):
    return Candidate(source="s", source_id=str(i), url=f"u/{i}", title=str(i))


def pages(*page_ids, fail_at=None):
    """A list whose page n has the cards page_ids[n - 1]."""
    asked = []

    def fetch(n):
        asked.append(n)
        if n == fail_at:
            return None
        ids = page_ids[n - 1]
        return [card(i) for i in ids], n < len(page_ids)
    fetch.asked = asked
    return fetch


def test_it_stops_when_the_best_k_are_known():
    # The same 3 cards lead both lists, so after page 1 no other card can reach them
    a = pages([1, 2, 3, 4, 5, 6], [7, 8], [9])
    b = pages([1, 2, 3, 9, 8, 7], [4, 5], [6])
    result = threshold_search([ResultList("a", a), ResultList("b", b)], k=3, max_pages=3)
    assert result.stop == "the best 3 are known" and result.pages == {"a": 1, "b": 1}
    assert [c.source_id for c in result.order][:3] == ["1", "2", "3"]


def test_it_reads_on_while_an_unseen_card_can_still_be_better():
    a = pages([1, 2], [3, 4], [5])
    b = pages([6, 7], [8, 9], [10])
    result = threshold_search([ResultList("a", a), ResultList("b", b)], k=3, max_pages=2)
    assert result.stop == "the page limit" and result.pages == {"a": 2, "b": 2}
    assert a.asked == [1, 2]


def test_all_read_when_the_lists_end():
    result = threshold_search([ResultList("a", pages([1, 2]))], k=5, max_pages=3)
    assert result.stop == "all results read" and result.pages == {"a": 1}


def test_a_list_that_does_not_load_is_left_out():
    result = threshold_search([ResultList("a", pages([1, 2], [3])), ResultList("b", pages([4], fail_at=1))],
                              k=10, max_pages=2)
    assert result.failed == ["b"] and result.pages == {"a": 2, "b": 0}
    assert [c.source_id for c in result.order] == ["1", "2", "3"]


def test_the_cards_of_the_first_lists_come_first():
    filtered = ResultList("filtered", pages([2, 3]))
    plain = ResultList("plain", pages([1, 2, 3]), first=False)
    result = threshold_search([filtered, plain], k=10, max_pages=1)
    # 1 has the best place of the plain list, but only the plain list has it
    assert [c.source_id for c in result.order] == ["2", "3", "1"]
    assert place_score(1) > place_score(2)
