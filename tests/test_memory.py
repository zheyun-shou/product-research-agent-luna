import copy
from datetime import datetime, timezone

import pytest

from scout.agent.loop import (Forget, Remember, RemoveProducts, RestoreProducts, Toolbox, UpdateNeeds,
                              context_text, step_detail, step_title)
from scout.needs import ConditionSpec
from tests.fakes import FakeLLM
from tests.test_loop import Shop

DAY = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.mark.db
def test_memories_of_a_shopper_are_kept_one_time_and_deleted_only_by_their_shopper(db):
    from scout.db.store import add_memory, forget_memories, memories
    first = add_memory(db, "me", "fact", "The  kitchen niche is 60 cm wide", "fridge", "t1")
    other = add_memory(db, "eval-x", "dislike", "No Bomann", None)
    assert [(m["kind"], m["text"], m["category"], m["source"]) for m in memories(db, "me")] == [
        ("fact", "The kitchen niche is 60 cm wide", "fridge", "remember")]
    # The same text again, for all categories: one memory, now for all categories
    assert add_memory(db, "me", "fact", "the kitchen niche is 60 cm wide", None) == first
    assert memories(db, "me")[0]["category"] is None
    assert forget_memories(db, "me", [first, other]) == [first]
    assert memories(db, "me") == [] and len(memories(db, "eval-x")) == 1


class Memory:
    def __init__(self):
        self.items = []

    def remember(self, kind, text, category, thread, source="remember"):
        self.items.append({"id": len(self.items) + 1, "kind": kind, "text": text, "category": category,
                           "source": source, "created_at": DAY})
        return len(self.items)

    def forget(self, ids):
        gone = [m["id"] for m in self.items if m["id"] in ids]
        self.items = [m for m in self.items if m["id"] not in ids]
        return gone


def deps_with_memory(shop=None):
    memory = Memory()
    deps = (shop or Shop()).deps(FakeLLM([]))
    deps.memories = lambda: copy.deepcopy(memory.items)
    deps.remember, deps.forget = memory.remember, memory.forget
    return deps, memory


def test_remember_and_forget_and_the_context():
    deps, memory = deps_with_memory()
    box = Toolbox(deps, {"needs": {"category": "fridge"}}, "t1")
    assert box.remember(Remember(kind="fact", text="The niche is 60 cm wide")) == "kept as memory 1 (fact, fridge)."
    assert box.remember(Remember(kind="dislike", text="No loud fans", all_categories=True)) == \
        "kept as memory 2 (dislike, all categories)."
    memory.remember("like", "Black headphones", "headphones", "t0")
    text = context_text({"needs": {"category": "fridge"}}, deps, "t1")
    assert "[memory 1] fact (fridge), 2026-10-14: The niche is 60 cm wide" in text
    assert "[memory 2] dislike, 2026-10-14: No loud fans" in text and "Black headphones" not in text
    assert "Black headphones" in context_text({}, deps, "t1")  # without a category, all memories
    assert "Categories with a profile: fridge (kinds fridge_freezer, " in context_text({}, deps, "t1")
    assert box.forget(Forget(ids=[1, 9])) == "deleted memory 1. There is no memory 9."
    assert box.forget(Forget(ids=[9])) == "there is no memory 9."
    assert step_detail("forget: there is no memory 9.")[0] == "fail"
    assert step_title(Remember(kind="like", text="x")) == ("needs", "Remember a like")
    assert step_title(Forget(ids=[1, 2])) == ("needs", "Forget 2 memories")


def test_a_number_in_a_memory_counts_as_written_by_the_shopper():
    deps, memory = deps_with_memory()
    memory.remember("fact", "The niche is 175 cm high", "fridge", "t0")
    needs = UpdateNeeds(category="fridge", conditions=[ConditionSpec(attribute="height", op="<=", value=175)])
    assert Toolbox(deps, {"needs": {}}, "t1").update_needs(needs).startswith("recorded")
    other = needs.model_copy(update={"conditions": [ConditionSpec(attribute="height", op="<=", value=170)]})
    assert Toolbox(deps, {"needs": {}}, "t1").update_needs(other).startswith("not recorded")
    # The numbers of the title of a removed product and of another category do not count
    memory.remember("dislike", "Fridge KG 170: too loud", "fridge", "t0", "removed")
    memory.remember("fact", "The desk is 170 cm wide", "desk", "t0")
    assert Toolbox(deps, {"needs": {}}, "t1").update_needs(other).startswith("not recorded")


def test_the_reason_of_a_removed_product_is_a_dislike():
    deps, memory = deps_with_memory()
    box = Toolbox(deps, {"needs": {"category": "fridge"}}, "t1")
    box.remove_products(RemoveProducts(ids=[2], reason="too loud", dislike=True))
    box.remove_products(RemoveProducts(ids=[3], reason="out of stock"))
    box.remove_products(RemoveProducts(ids=[4], dislike=True))
    assert [(m["kind"], m["text"], m["category"], m["source"]) for m in memory.items] == [
        ("dislike", "Fridge 2: too loud", "fridge", "removed")]
    # A product that comes back loses its dislike
    box = Toolbox(deps, {"needs": {"category": "fridge"}, **box.update}, "t1")
    box.restore_products(RestoreProducts(ids=[2]))
    assert memory.items == []
