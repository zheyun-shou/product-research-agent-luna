"""What the shopper wants: the kind of product, hard conditions, the budget and soft preferences."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.picks.rank import Condition
from scout.picks.write import numbers_in
from scout.profiles import CategoryProfile

# A number counts as said when the shopper wrote it, or wrote it in another unit of the same length or weight.
# Other units and the budget allow no change: "2 people" is not 200 l, and "60 cm" is not a budget of 6000.
UNIT_FACTORS = {
    "cm": (Decimal("1"), Decimal("0.1"), Decimal("100")),    # cm, mm, m
    "mm": (Decimal("1"), Decimal("10"), Decimal("1000")),    # mm, cm, m
    "kg": (Decimal("1"), Decimal("0.001")),                  # kg, g
}
SAME = (Decimal("1"),)


class ConditionSpec(BaseModel):
    attribute: str
    op: Literal["<=", ">="]
    value: float


class Needs(BaseModel):
    category: str | None = None
    kind: str | None = None
    conditions: list[ConditionSpec] = Field(default_factory=list)
    budget: float | None = None  # SEK
    budget_note: str | None = None  # a budget in another currency: the amount, the currency and the rate used
    preferences: list[str] = Field(default_factory=list)
    search_terms_sv: str | None = None
    web_tests: bool = True  # False: the shopper does not want web searches for tests (the floor of the picks)

    def budget_sek(self) -> Decimal | None:
        return None if self.budget is None else Decimal(str(self.budget))

    def rank_conditions(self) -> list[Condition]:
        return [Condition(c.attribute, c.op, c.value) for c in self.conditions]

    def text(self) -> str:
        parts = [f"{c.attribute}{c.op}{c.value:g}" for c in self.conditions]
        if self.budget is not None:
            parts.append(f"budget {self.budget:g} SEK" + (f" ({self.budget_note})" if self.budget_note else ""))
        parts += [f"prefers {p}" for p in self.preferences]
        if not self.web_tests:
            parts.append("no web search for tests")
        return ", ".join(parts)

    def wanted(self, profile: CategoryProfile) -> str:
        kind = profile.kind(self.kind)
        return kind.label if kind else profile.display_name

    def search_terms(self, profile: CategoryProfile) -> str:
        """One search phrase. Drafted profiles can list several with commas. amazon.se would look for all the words
        of the list at the same time, so only the first phrase is used."""
        kind = profile.kind(self.kind)
        terms = self.search_terms_sv or (kind.search_terms_sv if kind else None) \
            or profile.search_terms_sv or profile.category
        return terms.split(",")[0].strip()


def was_said(value: float, said: set[Decimal], factors: tuple[Decimal, ...] = SAME) -> bool:
    target = Decimal(str(value))
    return any(number * factor == target for number in said for factor in factors)


def _said_text(said: set[Decimal]) -> str:
    return "the shopper wrote: " + (", ".join(f"{n:g}" for n in sorted(said)) or "no numbers")


def check_needs(needs: Needs, profile: CategoryProfile, said: set[Decimal] | None = None) -> list[str]:
    """Return the problems. With `said`, each number must be one that the shopper wrote."""
    errors = []
    if needs.kind is not None and profile.kind(needs.kind) is None:
        errors.append(f"kind '{needs.kind}' is not one of {[k.name for k in profile.kinds]}")
    for cond in needs.conditions:
        attr = profile.attribute(cond.attribute)
        if attr is None:
            errors.append(f"'{cond.attribute}' is not an attribute in the profile")
        elif attr.type != "number":
            errors.append(f"'{cond.attribute}' is not a number attribute, so it cannot have a condition")
        elif said is not None and attr.kind == "hard" and not was_said(cond.value, said,
                                                                      UNIT_FACTORS.get(attr.unit or "", SAME)):
            errors.append(f"{cond.attribute} {cond.value:g} is not a number the shopper wrote "
                          f"({_said_text(said)}). Use one of them, or ask.")
    if needs.budget is not None:
        if needs.budget <= 0:
            errors.append("budget must be more than 0")
        elif said is not None and needs.budget_note and not (numbers_in(needs.budget_note) & said):
            errors.append(f"budget_note '{needs.budget_note}' must give the amount that the shopper wrote "
                          f"({_said_text(said)}), its currency and the rate")
        elif said is not None and not needs.budget_note and not was_said(needs.budget, said):
            errors.append(f"budget {needs.budget:g} is not a number the shopper wrote ({_said_text(said)}). "
                          "Use one of them, or ask.")
    return errors
