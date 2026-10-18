from datetime import datetime

from scout.eval.replay import Run, cards_of, interleave, rrf, snapshot_html, tiers
from scout.sources.amazon_se.urls import search_url
from scout.sources.base import Candidate


def card(i):
    return Candidate(source="amazon_se", source_id=i, url=f"https://www.amazon.se/dp/{i}", title=i)


def ids(cards):
    return [c.source_id for c in cards]


def test_interleave_takes_the_first_of_each_list_in_turn():
    assert ids(interleave([[card("A"), card("B")], [card("C"), card("A"), card("D")]])) == ["A", "C", "B", "D"]


def test_rrf_puts_a_card_of_two_lists_before_a_first_place_of_one():
    order = rrf([[card("A"), card("B")], [card("C"), card("B")]])
    assert ids(order)[0] == "B"


def test_tiers_and_cards_of_count_a_card_once():
    assert ids(tiers([card("A")], [card("A"), card("B")])) == ["A", "B"]
    assert ids(cards_of([[card("A"), card("B")], [card("B"), card("C")]], 1)) == ["A", "B"]
    assert ids(cards_of([[card("A"), card("B")], [card("B"), card("C")]])) == ["A", "B", "C"]


def test_snapshot_html_takes_the_newest_page_inside_the_run(tmp_path):
    url = search_url("löparskor", 1, "rating")
    folder = tmp_path / "amazon_se" / "search" / "löparskor-p1-review-rank"
    folder.mkdir(parents=True)
    for stamp, text in (("20261017T1000", "old"), ("20261017T1100", "in"), ("20261017T1300", "late")):
        (folder / f"{stamp}00.html").write_text(text, encoding="utf-8")
    run = Run("r", datetime(2026, 10, 17, 10, 30), datetime(2026, 10, 17, 12, 0))
    assert snapshot_html(tmp_path, url, run) == "in"
    assert snapshot_html(tmp_path, search_url("löparskor", 2), run) is None
