import pytest

from scout.extract.screen import Keep
from scout.llm.client import LLMUnavailable
from scout.needs import Needs
from scout.picks.pipeline import make_picks, shortlist
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile
from scout.sources.amazon_se.product import parse_product_page
from tests.fakes import FakeLLM
from tests.test_picks_db import FIXTURES, NOW, save

pytestmark = pytest.mark.db
FRIDGE = load_profile("fridge")
PAGES = {"B0AAAAAAA1": "product_full.html", "B0AAAAAAA3": "product_parts_price.html"}


def stock(db):
    for asin, name in PAGES.items():
        save(db, name, asin)


def refetch(card):
    html = (FIXTURES / PAGES[card.url[-10:]]).read_text(encoding="utf-8")
    return parse_product_page(html, card.url, NOW)


def test_shortlist_leaves_out_rejected_products_and_screens_for_the_kind(db):
    stock(db)
    everything = shortlist(db, FRIDGE, Needs(category="fridge"), None)
    assert len(everything.ranked) == 2 and "Titles were not screened" in everything.notes[0]
    first = everything.ranked[0]
    llm = FakeLLM([Keep(keep=[0])])
    short = shortlist(db, FRIDGE, Needs(category="fridge", kind="fridge_freezer"), llm, excluded={first.product_id})
    assert [c.product_id for c in short.ranked] == [everything.ranked[1].product_id]
    assert "The shopper wants: Fridge with a freezer part" in llm.requests[0][1]


def test_make_picks_rechecks_and_uses_the_needs_text(db):
    stock(db)
    needs = Needs(category="fridge", budget=9000)
    result = make_picks(shortlist(db, FRIDGE, needs, None), needs, 1, refetch, None)
    assert len(result.chosen) == 1 and result.conditions_text == "budget 9000 SEK"
    assert result.render().startswith("Conditions: budget 9000 SEK")


def test_cards_by_ids_keep_the_given_order(db):
    stock(db)
    ids = [c.listing_id for c in shortlist(db, FRIDGE, Needs(category="fridge"), None).ranked]
    assert [c.listing_id for c in load_cards_by_ids(db, ids[::-1])] == ids[::-1]
    assert load_cards_by_ids(db, [999]) == []


def test_a_title_is_screened_one_time(db):
    stock(db)
    screened = {}
    llm = FakeLLM([Keep(keep=[0, 1])])
    needs = Needs(category="fridge", kind="fridge_freezer")
    first = shortlist(db, FRIDGE, needs, llm, screened=screened)
    again = shortlist(db, FRIDGE, needs, llm, screened=screened)
    assert len(llm.requests) == 1 and [c.listing_id for c in again.ranked] == [c.listing_id for c in first.ranked]


def test_kept_cards_need_no_new_check_and_first_cards_are_checked_first(db):
    stock(db)
    needs = Needs(category="fridge")
    short = shortlist(db, FRIDGE, needs, None)
    top, second = short.ranked
    checked = []

    def counting(card):
        checked.append(card.listing_id)
        return refetch(card)

    kept = make_picks(short, needs, 2, counting, None, kept=[top])
    assert [c.listing_id for c in kept.chosen] == [top.listing_id, second.listing_id]
    assert checked == [second.listing_id]
    checked.clear()
    make_picks(short, needs, 2, counting, None, first=[second])
    assert checked == [second.listing_id, top.listing_id]
    checked.clear()
    back = make_picks(short, needs, 1, counting, None, kept=[top], first=[second])  # a product came back
    assert [c.listing_id for c in back.chosen] == [second.listing_id] and checked == [second.listing_id]


def test_without_the_page_text_there_is_no_size_check(db):
    stock(db)
    needs = Needs(category="fridge")
    llm = FakeLLM([LLMUnavailable("busy")])  # only the summaries ask the model
    result = make_picks(shortlist(db, FRIDGE, needs, None), needs, 2, refetch, llm, size_check=False)
    assert [role for role, _ in llm.requests] == ["chat"]
    assert "Size check skipped: the pages were not fetched again." in result.notes
