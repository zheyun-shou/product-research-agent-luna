from decimal import Decimal

from scout.eval.hidden import SearchCase
from scout.eval.search import NEW, OLD, not_valid, search_target, summary_text
from scout.needs import ConditionSpec
from scout.profiles import load_profile
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import search_url
from scout.sources.base import Candidate, FilterGroup, FilterOption, ProductRecord
from tests.fakes import NOW
from tests.test_eval_hidden import TARGET

PROFILE = load_profile("fridge")
CASE = SearchCase(words="kyl frys", budget=5000, conditions=[ConditionSpec(attribute="width", op="<=", value=56)],
                  filters=["Colour: Red"])
GROUPS = [FilterGroup(key="c", name="Colour", options=[FilterOption(name="Red", value="c:1")])]


def cards(*ids, title="Fridge"):
    return [Candidate(source="amazon_se", source_id=i, url=f"u/{i}", title=f"{title} {i}", price=Decimal("4000"))
            for i in ids]


def test_the_place_of_the_target_before_and_with_the_filters():
    target = TARGET.model_copy(update={"search": CASE})
    wide = cards("W1", title="Fridge 70 cm wide")
    pages = {search_url("kyl frys", 1, sort): (wide + cards(*[f"X{n}" for n in range(12)], "B1"), False)
             for sort in (None, "rating", "sales")}
    pages[search_url("kyl frys", 1, None, ["p_36:-500000", "c:1"])] = (cards("B1"), False)
    for sort in ("rating", "sales"):
        pages[search_url("kyl frys", 1, sort, ["p_36:-500000", "c:1"])] = (cards("B1"), False)
    asked = []

    def fetch(url):
        asked.append(url)
        return pages.get(url)
    rows = search_target(target, PROFILE, fetch, AmazonSeAdapter(None, None), lambda: GROUPS, list_pages=2)
    assert rows[OLD]["place"] == 14 and rows[OLD]["calls"] == 2  # behind the wide one and 12 others
    assert rows[NEW]["place"] == 1 and rows[NEW]["filters"] == ["p_36:-500000", "c:1"]
    assert rows[NEW]["candidates"] == 13  # the wide one is outside the width
    text = summary_text([{"target": "t", **rows}, {"target": "u", "valid": False, "why": "out of stock"}])
    assert "t: before: place 14 of 14 (call 2); with filters: place 1 of 13 (call 1)" in text
    assert "u: not valid now (out of stock)" in text and "with filters: in the first 8 product pages for 1 of 1" in text


def test_a_target_that_does_not_fit_its_search_now():
    target = TARGET.model_copy(update={"search": CASE})

    def rec(**kw):
        return ProductRecord(source="amazon_se", source_id="B1", url="u", title="t", fetched_at=NOW, **kw)
    assert not_valid(target, rec(price=Decimal("7701.62"), in_stock=True)).startswith("the price is 7701.62")
    assert not_valid(target, rec(price=Decimal("4000"), in_stock=False)) == "out of stock"
    assert not_valid(target, rec(price=Decimal("4000"), in_stock=True,
                                 attributes={"width": {"value": 60}})) == "width is 60, not <= 56"
    assert not_valid(target, rec(price=Decimal("4000"), in_stock=True)) is None
