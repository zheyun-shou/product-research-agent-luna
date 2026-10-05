"""The calculation tool. The chat model writes the steps. Python does the arithmetic and shows the formula."""
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.agent.followup import card_text
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import card_facts, unchecked_numbers
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You change a shopper's calculation request into steps. You never calculate yourself.
Each step has a name, an operation (add, subtract, multiply or divide) and two operands, a and b.
An operand is one of these:
- one number that the shopper wrote, copied into number, for example "499". Never a formula or a unit.
- a fact of a pick, written into ref as "pick N field", for example "pick 1 price"
- the name of an earlier step, written into ref
For a percentage, divide by 100 in a step. result is the name of the step with the answer.
explanation is one short sentence in English that says what the result is. It has no numbers.
If the request needs a number that the shopper did not give, such as an exchange rate,
return no steps and ask for that number in explanation."""

CONSTANTS = {Decimal("100")}  # for percentages
PLAIN_NUMBER = re.compile(r"-?\d[\d\u00a0\u202f ]*(?:[.,]\d+)?")
SIGNS = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}


class CalcError(ValueError):
    pass


class Operand(BaseModel):
    number: str | None = None
    ref: str | None = None


class Step(BaseModel):
    name: str
    op: Literal["add", "subtract", "multiply", "divide"]
    a: Operand
    b: Operand


class CalcPlan(BaseModel):
    steps: list[Step] = Field(default_factory=list)
    result: str = ""
    explanation: str


def fmt(number: Decimal) -> str:
    return format(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize(), "f")


def run_plan(plan: CalcPlan, cards: list[Card], said: set[Decimal]) -> tuple[Decimal, list[str]]:
    values: dict[str, Decimal] = {}
    lines: list[str] = []

    def operand(op: Operand) -> tuple[Decimal, str]:
        if op.number is not None:
            number = to_decimal(op.number) if PLAIN_NUMBER.fullmatch(op.number.strip()) else None
            if number is None:
                raise CalcError(f"number '{op.number}' is not one plain number. Put a fact of a pick into ref, "
                                "and a number of the shopper into number")
            if number not in said:
                raise CalcError(f"{op.number} is not a number that the shopper wrote")
            return number, fmt(number)
        ref = (op.ref or "").strip()
        if ref in values:
            return values[ref], ref
        parts = ref.split()
        if len(parts) == 3 and parts[0] == "pick" and parts[1].isdigit() and 1 <= int(parts[1]) <= len(cards):
            fact = card_facts(cards[int(parts[1]) - 1]).get(parts[2])
            if isinstance(fact, Decimal):
                return fact, f"{fmt(fact)} ({ref})"
        raise CalcError(f"'{ref}' is not a number on a pick or an earlier step")

    for step in plan.steps:
        (a, a_text), (b, b_text) = operand(step.a), operand(step.b)
        if step.op == "add":
            value = a + b
        elif step.op == "subtract":
            value = a - b
        elif step.op == "multiply":
            value = a * b
        elif b == 0:
            raise CalcError("division by zero")
        else:
            value = a / b
        values[step.name] = value
        lines.append(f"{step.name} = {a_text} {SIGNS[step.op]} {b_text} = {fmt(value)}")
    if plan.result not in values:
        raise CalcError(f"the result '{plan.result}' is not one of the steps")
    return values[plan.result], lines


def calculate(llm, cards: list[Card], history: str, said: set[Decimal], max_rounds: int = 3) -> str:
    base = "Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1)) + f"\n\nConversation:\n{history}"
    plan, errors = None, []
    for _ in range(max_rounds):
        plan = llm.structured("chat", SYSTEM, retry_prompt(base, plan, errors), CalcPlan)
        if not plan.steps:
            llm.mark_checked(True)
            return plan.explanation
        try:
            _, lines = run_plan(plan, cards, said | CONSTANTS)
        except CalcError as exc:
            errors = [str(exc)]
            llm.mark_checked(False)
            continue
        llm.mark_checked(True)
        allowed = said | {Decimal(n) for n in range(1, len(cards) + 1)}
        explanation = plan.explanation if not unchecked_numbers(plan.explanation, allowed) else "Result:"
        return "\n".join([explanation, *lines, "Python did the arithmetic. Results are rounded to 2 decimals."])
    return (f"I could not turn this request into steps that Python can check ({errors[0]}). "
            "You can write the calculation in other words.")
