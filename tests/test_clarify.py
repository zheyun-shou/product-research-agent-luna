from decimal import Decimal

import pytest

from scout.agent.clarify import (CategoryChoice, ClarifyReply, DraftProfile, check_draft, clarify,
                                 draft_profile, pick_category)
from scout.llm.client import LLMOutputError
from scout.needs import ConditionSpec, Needs
from scout.profiles import ProductKind, ProfileAttribute, load_profile
from tests.fakes import FakeLLM

FRIDGE = load_profile("fridge")


def reply(ready, question="", kind=None, conditions=(), budget=None):
    return ClarifyReply(needs=Needs(kind=kind, conditions=list(conditions), budget=budget), ready=ready,
                        question=question)


def test_a_question_comes_back_while_the_kind_is_open():
    llm = FakeLLM([reply(False, "哪种冰箱？")])
    out = clarify(llm, FRIDGE, Needs(category="fridge"), "Shopper: 我想买冰箱", set())
    assert (out.ready, out.question, out.needs.category) == (False, "哪种冰箱？", "fridge")
    assert "- fridge_freezer: Fridge with a freezer part" in llm.requests[0][1]


def test_an_invented_number_is_sent_back_and_fixed():
    height = ConditionSpec(attribute="height", op="<=", value=185)
    llm = FakeLLM([reply(True, kind="fridge_freezer", conditions=[height]),
                   reply(True, kind="fridge_freezer", conditions=[height.model_copy(update={"value": 186})])])
    out = clarify(llm, FRIDGE, Needs(category="fridge"), "Shopper: höjd 186", {Decimal("186")})
    assert out.needs.conditions[0].value == 186
    assert "height 185 is not a number the shopper wrote" in llm.requests[1][1]


def test_ready_without_a_kind_is_not_accepted():
    with pytest.raises(LLMOutputError):
        clarify(FakeLLM([reply(True)] * 3), FRIDGE, Needs(category="fridge"), "Shopper: search now", set())


def test_pick_category_accepts_only_known_names_and_shows_the_kinds():
    fridge = load_profile("fridge")
    llm = FakeLLM([CategoryChoice(category="fridge")])
    assert pick_category(llm, "Shopper: 酒柜", [fridge]).category == "fridge"
    assert "- fridge (Fridge). Kinds: Fridge with a freezer part;" in llm.requests[0][1]
    assert "Wine cooler" in llm.requests[0][1]
    assert pick_category(FakeLLM([CategoryChoice(category="sofa")]), "Shopper: soffa", [fridge]).category is None
    choice = pick_category(FakeLLM([CategoryChoice(new_category="sofa")]), "Shopper: soffa", [fridge])
    assert (choice.category, choice.new_category) == (None, "sofa")


HEADPHONES = DraftProfile(
    category="headphones", display_name="Headphones", search_terms_sv="hörlurar",
    kinds=[ProductKind(name="over_ear", label="Over-ear headphones", search_terms_sv="over-ear hörlurar")],
    attributes=[ProfileAttribute(name="battery_life", label="Battery life", type="number", unit="h", kind="soft"),
                ProfileAttribute(name="noise_cancelling", label="Noise cancelling", type="boolean", kind="soft")],
    questions_for_user=["Do you need noise cancelling?"])


def test_a_draft_profile_is_checked_and_marked_as_a_draft():
    bad = HEADPHONES.model_copy(update={"category": "Hörlurar", "attributes": [
        ProfileAttribute(name="price", label="Price", type="number", kind="hard")]})
    errors = check_draft(bad, ["fridge"])
    assert any("lowercase" in e for e in errors) and any("at least two attributes" in e for e in errors)
    assert any("price is a number, so it needs a unit" in e for e in errors)
    assert any("the system has price already" in e for e in errors)
    llm = FakeLLM([bad, HEADPHONES])
    profile = draft_profile(llm, "headphones", "Shopper: hörlurar", ["fridge"])
    assert profile.draft and profile.category == "headphones" and profile.sources == {"products": ["amazon_se"]}
    assert [c.checked for c in llm.calls] == [False, True]