"""Ask the worker model for fields the selectors missed, keep only values whose raw text checks out."""
import re
from decimal import Decimal

from pydantic import BaseModel

from scout.extract.verify import normalise, verify_raw
from scout.profiles import CategoryProfile, ProfileAttribute
from scout.sources.amazon_se.numbers import NUMBER, parse_price, to_decimal
from scout.sources.base import ProductRecord

SYSTEM = """You read one product page from amazon.se and pull out the requested fields.
Rules:
- Use only the page text you are given. Never guess.
- For each field, copy into "raw" the exact short piece of page text the value comes from, character for character.
  Include the label and the unit, for example "Höjd: 1780 mm", "80D x 45B x 48H centimeter" or "35 dB".
  Never give a bare number or letter.
  When one line gives several sizes, copy the whole line, with its unit, for each of them.
- If the page does not state a field, set value and raw to null.
- Give numbers with a dot as decimal separator, converted to the unit asked for. 1780 mm is 178 when the unit is cm.
- For yes or no fields answer "true" or "false"."""

CORE = {
    "title": "the product title",
    "brand": "the brand name",
    "model": "the manufacturer's model number",
    "price": "the price you pay now for this item, in SEK, as a number",
}


class NamedValue(BaseModel):
    name: str
    value: str | None = None
    raw: str | None = None


class PageExtraction(BaseModel):
    values: list[NamedValue]


def _describe(attr: ProfileAttribute) -> str:
    if attr.type == "number":
        return f"{attr.label}, number in {attr.unit}" if attr.unit else f"{attr.label}, number"
    if attr.type == "enum":
        return f"{attr.label}, one of {', '.join(attr.values or [])}"
    if attr.type == "boolean":
        return f"{attr.label}, true or false"
    return f"{attr.label}, text"


def build_prompt(rec: ProductRecord, core: list[str], attrs: list[ProfileAttribute]) -> str:
    lines = ["Fields:"]
    lines += [f"- {name}: {CORE[name]}" for name in core]
    lines += [f"- {attr.name}: {_describe(attr)}" for attr in attrs]
    lines += ["", "Page text:", rec.text]
    return "\n".join(lines)


def convert(attr: ProfileAttribute, value: str):
    text = value.strip()
    if attr.type == "number":
        number = to_decimal(text)
        return float(number) if number is not None else None
    if attr.type == "enum":
        return next((option for option in attr.values or [] if option.casefold() == text.casefold()), None)
    if attr.type == "boolean":
        return {"true": True, "yes": True, "ja": True, "false": False, "no": False, "nej": False}.get(text.casefold())
    return text or None


# Unit words that amazon.se pages use, with the factor to the profile unit.
UNIT_WORDS = {
    "cm": {"mm": "0.1", "millimeter": "0.1", "cm": "1", "centimeter": "1", "m": "100", "meter": "100"},
    "l": {"l": "1", "liter": "1", "liters": "1", "litre": "1", "litres": "1"},
    "db": {"db": "1"},
}


def _unit_factors(unit: str, raw: str) -> list[Decimal]:
    words = UNIT_WORDS[unit.casefold()]
    # Digits may touch the word ("46L", "1780mm"), letters may not ("m" in "centimeter").
    found = re.findall(r"(?<![^\W\d_])([^\W\d_]+)(?![^\W\d_])", raw)
    return [Decimal(words[w.casefold()]) for w in found if w.casefold() in words]


def shows_value(attr: ProfileAttribute, value, raw: str) -> bool:
    """The raw text must show this value, not only exist somewhere on the page."""
    if attr.type == "number":
        if attr.unit and attr.unit.casefold() in UNIT_WORDS:
            factors = _unit_factors(attr.unit, raw)  # "48H" alone could be cm or mm
        else:
            factors = [Decimal(1)] if re.search(r"[^\W\d_]", raw) else []  # a bare number shows no field
        target = Decimal(str(value))
        numbers = [to_decimal(group) for group in NUMBER.findall(raw)]
        return any(n is not None and n * f == target for n in numbers for f in factors)
    if attr.type == "enum":
        # A whole token, so "A+" on the old EU scale does not count as the new class A.
        token = re.search(rf"(?<![\w+]){re.escape(value)}(?![\w+])", raw, re.I)
        return bool(token) and len(normalise(raw)) > len(normalise(value))
    return True


def enrich_record(rec: ProductRecord, profile: CategoryProfile, llm) -> tuple[ProductRecord, bool]:
    core = [name for name in CORE if getattr(rec, name) is None]
    attrs = [attr for attr in profile.attributes if attr.name not in rec.attributes]
    if not rec.text or (not core and not attrs):
        return rec, False
    result = llm.structured("worker", SYSTEM, build_prompt(rec, core, attrs), PageExtraction)
    updates: dict = {}
    attributes = dict(rec.attributes)
    llm_fields = list(rec.llm_fields)
    wanted = {attr.name: attr for attr in attrs}
    for item in result.values:
        if item.value is None or not item.raw or not verify_raw(item.raw, rec.text):
            continue
        if item.name in core:
            if item.name == "price":
                price = to_decimal(item.value)
                if price is None or parse_price(item.raw)[0] != price:
                    continue
                updates.update(price=price, currency="SEK")
            else:
                if normalise(item.value) not in normalise(item.raw):
                    continue
                updates[item.name] = item.value.strip()
            llm_fields.append(item.name)
        elif item.name in wanted:
            attr = wanted[item.name]
            value = convert(attr, item.value)
            if value is None or not shows_value(attr, value, item.raw):
                continue
            attributes[attr.name] = {"value": value, "unit": attr.unit, "raw": item.raw, "source_url": rec.url}
    return rec.model_copy(update={**updates, "attributes": attributes, "llm_fields": llm_fields}), True
