"""Filters of the shop from the needs, and Python's own check of the values on the search cards.

Python selects the number filters: for each condition, all options whose range overlaps the allowed range. A card
value is checked by Python too, because other shops can have no filters, and a shop can tag a product wrongly."""
import re
from decimal import Decimal

from scout.needs import Needs
from scout.profiles import CategoryProfile, ProfileAttribute
from scout.sources.base import Candidate, FilterGroup, FilterOption

UNITS = {"cm": {"cm": 1.0, "mm": 0.1, "m": 100.0}, "l": {"l": 1.0}}
_NUMBER = r"(\d+(?:[.,]\d+)?)\s*"


def _group_for(attr: ProfileAttribute, groups: list[FilterGroup]) -> FilterGroup | None:
    name = (attr.site_filter or attr.label).lower()
    return next((g for g in groups if g.numeric and g.name.lower() == name), None)


def _overlaps(option: FilterOption, low: float, high: float) -> bool:
    return (option.low is None or option.low <= high) and (option.high is None or option.high >= low)


def number_filters(needs: Needs, profile: CategoryProfile, groups: list[FilterGroup]) -> list[str]:
    """The option values for the conditions of the needs. A group gives no filter when its unit is not the unit of
    the attribute, when no option fits (Python's check then decides), or when all options fit."""
    out = []
    attrs = {a.name: a for a in profile.attributes}
    for name in dict.fromkeys(c.attribute for c in needs.conditions):
        attr = attrs.get(name)
        group = _group_for(attr, groups) if attr and attr.type == "number" else None
        if group is None or any((o.unit or "").lower() != (attr.unit or "").lower() for o in group.options):
            continue
        conds = [c for c in needs.conditions if c.attribute == name]
        low = max((c.value for c in conds if c.op == ">="), default=float("-inf"))
        high = min((c.value for c in conds if c.op == "<="), default=float("inf"))
        chosen = [o for o in group.options if _overlaps(o, low, high)] if low <= high else []
        if chosen and len(chosen) < len(group.options):
            out += [o.value for o in chosen]
    return out


def title_values(title: str, profile: CategoryProfile) -> dict[str, float]:
    """The values that a search title gives with a word next to them ("170 cm High", "Height 180 cm", "54 cm
    breit"), in the unit of the attribute. A value given two different ways is left out."""
    values = {}
    for attr in profile.attributes:
        factors = UNITS.get((attr.unit or "").lower())
        if attr.type != "number" or not attr.title_words or not factors:
            continue
        words = "|".join(map(re.escape, attr.title_words))
        units = "|".join(factors)
        # Only a space (or a colon after the word) between them: in "55 cm, höjd 1,77 m" the 55 cm is not the height
        pattern = re.compile(rf"\b(?:{words})\b:? {_NUMBER}({units})\b|{_NUMBER}({units}) (?:{words})\b", re.I)
        found = set()
        for m in pattern.finditer(title):
            number, unit = (m[1], m[2]) if m[1] else (m[3], m[4])
            found.add(round(float(number.replace(",", ".")) * factors[unit.lower()], 2))
        if len(found) == 1:
            values[attr.name] = found.pop()
    return values


def off_condition(card: Candidate, needs: Needs, profile: CategoryProfile) -> bool:
    """True when a value of the card is outside a condition of the needs. Unknown values pass."""
    values = title_values(card.title, profile)
    for c in needs.conditions:
        value = values.get(c.attribute)
        if value is not None and (value > c.value if c.op == "<=" else value < c.value):
            return True
    return False


def within_budget(card: Candidate, budget: Decimal | None) -> bool:
    return budget is None or card.price is None or card.price <= budget
