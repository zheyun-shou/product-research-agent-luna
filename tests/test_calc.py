from decimal import Decimal

import pytest

from scout.agent.calc import CalcError, CalcPlan, Operand, Step, calculate, run_plan
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

CARDS = [card(1)]  # price 5000 SEK
PRICE = Operand(ref="pick 1 price")


def plan(*steps, result="total", explanation="Price with shipping."):
    return CalcPlan(steps=[Step(name=n, op=op, a=a, b=b) for n, op, a, b in steps], result=result,
                    explanation=explanation)


def test_price_plus_shipping_shows_the_formula():
    value, lines = run_plan(plan(("total", "add", PRICE, Operand(number="499"))), CARDS, {Decimal("499")})
    assert value == Decimal("5499")
    assert lines == ["total = 5000 (pick 1 price) + 499 = 5499"]


def test_a_percentage_uses_earlier_steps():
    steps = plan(("vat", "multiply", PRICE, Operand(number="25")),
                 ("vat_part", "divide", Operand(ref="vat"), Operand(number="100")),
                 ("total", "add", PRICE, Operand(ref="vat_part")))
    value, lines = run_plan(steps, CARDS, {Decimal("25"), Decimal("100")})
    assert value == Decimal("6250") and lines[1] == "vat_part = vat / 100 = 1250"


def test_a_number_the_shopper_did_not_write_is_refused():
    with pytest.raises(CalcError, match="11.5 is not a number that the shopper wrote"):
        run_plan(plan(("total", "multiply", PRICE, Operand(number="11.5"))), CARDS, set())


def test_unknown_refs_and_division_by_zero_are_refused():
    with pytest.raises(CalcError, match="'pick 4 price'"):
        run_plan(plan(("total", "add", Operand(ref="pick 4 price"), Operand(number="1"))), CARDS, {Decimal("1")})
    with pytest.raises(CalcError, match="division by zero"):
        run_plan(plan(("total", "divide", PRICE, Operand(number="0"))), CARDS, {Decimal("0")})


def test_calculate_asks_for_a_missing_rate_and_shows_the_python_result():
    ask = CalcPlan(steps=[], result="", explanation="Which exchange rate do you want to use?")
    assert calculate(FakeLLM([ask]), CARDS, "Shopper: the price in euro?", set()) == ask.explanation
    text = calculate(FakeLLM([plan(("total", "add", PRICE, Operand(number="499")))]), CARDS,
                     "Shopper: plus 499 for shipping", {Decimal("499")})
    assert text.splitlines()[:2] == ["Price with shipping.", "total = 5000 (pick 1 price) + 499 = 5499"]


def test_a_formula_in_number_goes_back_to_the_model():
    formula = plan(("total", "add", Operand(number="(pick 1 price) + 499 = 5499"), Operand(number="499")))
    good = plan(("total", "add", PRICE, Operand(number="499")))
    llm = FakeLLM([formula, good])
    text = calculate(llm, CARDS, "Shopper: plus 499 for shipping", {Decimal("499")})
    assert "total = 5000 (pick 1 price) + 499 = 5499" in text
    assert "is not one plain number" in llm.requests[1][1] and "Your last answer" in llm.requests[1][1]
    assert "could not turn this request" in calculate(FakeLLM([formula] * 3), CARDS, "Shopper: plus 499",
                                                      {Decimal("499")})
