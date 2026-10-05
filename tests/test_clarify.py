from decimal import Decimal

import pytest

from scout.agent.clarify import CategoryChoice, ClarifyReply, clarify, pick_category
from scout.llm.client import LLMOutputError
from scout.needs import ConditionSpec, Needs
from scout.profiles import load_profile
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
    assert pick_category(llm, "Shopper: 酒柜", [fridge]) == "fridge"
    assert "- fridge (Fridge). Kinds: Fridge with a freezer part;" in llm.requests[0][1]
    assert "Wine cooler" in llm.requests[0][1]
    assert pick_category(FakeLLM([CategoryChoice(category="sofa")]), "Shopper: soffa", [fridge]) is None