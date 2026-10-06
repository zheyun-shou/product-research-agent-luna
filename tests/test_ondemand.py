from dataclasses import dataclass

import pytest

from scout.extract.ondemand import Found, FoundValue, Store, fill_missing
from scout.picks.rank import Condition
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

FRIDGE = load_profile("fridge")
HEIGHT = [Condition("height", "<=", 180)]


@dataclass
class Hit:
    chunk_id: int
    text: str


class FakeStore:
    def __init__(self, chunks=None, searched=()):
        self.chunks = chunks if chunks is not None else {
            1: [Hit(11, "Product: Fridge 1\nHöjd: 1780 mm\nBredd: 595 mm"), Hit(12, "Product: Fridge 1\nTyst")]}
        self.saved, self.marked, self.done, self.queries = [], [], set(searched), []

    def store(self):
        return Store(retrieve=self.retrieve, save=lambda p, n, e: self.saved.append((p, n, e)),
                     searched=lambda ids: self.done, mark=lambda p, n, f: self.marked.append((p, n, f)))

    def retrieve(self, listing_id, query, k):
        self.queries.append((listing_id, query))
        return self.chunks.get(listing_id, [])


def found(*values):
    return Found(values=[FoundValue(**v) for v in values])


def test_a_checked_value_is_kept_with_its_chunk():
    fake, cards = FakeStore(), [card(1, 4.5, 100)]
    llm = FakeLLM([found({"name": "height", "value": "178", "raw": "Höjd: 1780 mm", "chunk_id": 11})])
    notes = fill_missing(cards, HEIGHT, FRIDGE, llm, fake.store())
    entry = cards[0].attributes["height"]
    assert (entry["value"], entry["raw"], entry["source_chunk_id"]) == (178.0, "Höjd: 1780 mm", 11)
    assert fake.saved == [(1, "height", entry)] and fake.marked == [(1, "height", True)]
    assert notes == ["Looked for missing values of your conditions in the text of 1 products: found 1."]
    assert "[chunk 11]" in llm.requests[0][1] and fake.queries == [(1, "Height (cm)")]


@pytest.mark.parametrize("value", [
    {"name": "height", "value": "178", "raw": "Höjd: 1780 mm", "chunk_id": 12},  # raw text is not in that chunk
    {"name": "height", "value": "178", "raw": "Höjd: 1780 mm", "chunk_id": 99},  # a chunk that was not given
    {"name": "height", "value": "175", "raw": "Höjd: 1780 mm", "chunk_id": 11},  # raw text shows another value
    {"name": "height", "value": "59.5", "raw": "Bredd: 595", "chunk_id": 11},  # no unit word
    {"name": "height", "value": None, "raw": None, "chunk_id": None},
])
def test_a_value_that_fails_a_check_is_left_out(value):
    fake, cards = FakeStore(), [card(1, 4.5, 100)]
    fill_missing(cards, HEIGHT, FRIDGE, FakeLLM([found(value)]), fake.store())
    assert "height" not in cards[0].attributes
    assert fake.saved == [] and fake.marked == [(1, "height", False)]


def test_no_model_call_when_searched_on_this_text_or_no_chunks():
    fake = FakeStore(chunks={}, searched={(1, "height")})
    assert fill_missing([card(1), card(2)], HEIGHT, FRIDGE, FakeLLM([]), fake.store()) == []
    assert fake.queries == [(2, "Height (cm)")] and fake.marked == [(2, "height", False)]


def test_only_cards_that_miss_a_value_and_at_most_max_cards():
    fake = FakeStore(chunks={i: [Hit(i, "x")] for i in range(1, 6)})
    cards = [card(1, height=170), card(2), card(3), card(4)]
    fill_missing(cards, HEIGHT, FRIDGE, FakeLLM([found(), found()]), fake.store(), max_cards=2)
    assert [q[0] for q in fake.queries] == [2, 3]


@pytest.mark.db
def test_store_functions(db):
    from scout.db.store import attribute_searches, mark_attribute_search, save_attribute
    from tests.test_picks_db import save
    save(db, "product_full.html", "B0AAAAAAA1")
    product = db.execute("SELECT id FROM products").fetchone()["id"]
    save_attribute(db, product, "noise", {"value": 35.0, "unit": "dB", "raw": "35 dB", "source_chunk_id": 1})
    attrs = db.execute("SELECT attributes FROM products WHERE id = %s", (product,)).fetchone()["attributes"]
    assert attrs["noise"]["value"] == 35.0
    mark_attribute_search(db, product, "noise", True)
    assert attribute_searches(db, [product]) == {(product, "noise")}
    db.execute("UPDATE chunks SET fetched_at = now() + interval '1 day'")  # a newer page was stored
    db.commit()
    assert attribute_searches(db, [product]) == set()
