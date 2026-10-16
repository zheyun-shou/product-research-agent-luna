"""Filters of the shop from the needs, and Python's own check of the values on the search cards.

Python selects the number filters: for each condition, all options whose range overlaps the allowed range. A card
value is checked by Python too, because other shops can have no filters, and a shop can tag a product wrongly."""
import re

from scout.needs import ConditionSpec, Needs
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


def outside(value: float, condition: ConditionSpec) -> bool:
    return value > condition.value if condition.op == "<=" else value < condition.value


def off_condition(card: Candidate, needs: Needs, profile: CategoryProfile) -> bool:
    """True when a value of the card is outside a condition of the needs. Unknown values pass."""
    values = title_values(card.title, profile)
    return any(values.get(c.attribute) is not None and outside(values[c.attribute], c) for c in needs.conditions)


def word_filters(names: list[str], groups: list[FilterGroup]) -> tuple[list[str], list[str]]:
    """The option values of filters given as "Group: Option", and the names that are not on the pages."""
    options = {f"{g.name}: {o.name}".lower(): o.value for g in groups if not g.numeric for o in g.options}
    values, unknown = [], []
    for name in names:
        value = options.get(" ".join(name.split()).lower())
        (values if value else unknown).append(value or name)
    return values, unknown


def filter_names(values: list[str], groups: list[FilterGroup]) -> list[str]:
    """The values of filters as "Group: Option", for the result text."""
    names = {o.value: f"{g.name}: {o.name}" for g in groups for o in g.options}
    return [names.get(v, "price up to the budget" if v.startswith("p_36:") else v) for v in values]


def word_filters_text(groups: list[FilterGroup], per_group: int = 12) -> str:
    lines = [f"{g.name}: {', '.join(o.name for o in g.options[:per_group])}" for g in groups if not g.numeric]
    return "; ".join(lines)
