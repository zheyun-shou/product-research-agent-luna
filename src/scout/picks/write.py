"""The chat model writes the picks; Python checks every id and number against the cards."""
import json
import re
from decimal import Decimal
from typing import Sequence

from pydantic import BaseModel

from scout.llm.feedback import retry_prompt
from scout.picks.rank import PRIOR_WEIGHT, Card
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You write short product recommendations for a shopper.
You get the shopper's conditions and product cards. Each card has an id and facts.
Rules:
- Write one pick per card, in the order given, with the card's id as listing_id.
- summary: one or two sentences on why the product fits, and what to watch out for, such as missing data.
- Use only facts on the cards.
- Every number you write in a summary must also be in that pick's claims, with the card field it comes from.
  Claim fields are price, rating, review_count, or an attribute name on the card.
  You may also repeat numbers from the shopper's conditions without a claim. Do not write any other numbers.
- Write in {language}."""

SUMMARY_NUMBER = re.compile(r"\d{1,3}(?:[\u00a0\u202f ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
RATING_SCALE = re.compile(r"(?:/|\bout of|\bav|\bof)\s*5\b", re.IGNORECASE)  # the "5" in "4.5/5" is not a claim


class Claim(BaseModel):
    field: str
    value: str


class PickText(BaseModel):
    listing_id: int
    summary: str
    claims: list[Claim]


class PicksText(BaseModel):
    picks: list[PickText]


YES_NO = {"true": "yes", "yes": "yes", "ja": "yes", "false": "no", "no": "no", "nej": "no"}


def _as_text(value: str) -> str:
    plain = value.strip().lower()
    return YES_NO.get(plain, plain)


def card_facts(card: Card) -> dict[str, Decimal | str]:
    """Numbers are compared as numbers. Yes/no and class values are compared as text."""
    facts: dict[str, Decimal | str] = {}
    if card.price is not None:
        facts["price"] = Decimal(card.price)
    if card.rating is not None:
        facts["rating"] = Decimal(card.rating)
    if card.review_count is not None:
        facts["review_count"] = Decimal(card.review_count)
    for name, entry in card.attributes.items():
        value = entry.get("value")
        if isinstance(value, bool):
            facts[name] = "yes" if value else "no"
        elif isinstance(value, (int, float)):
            facts[name] = Decimal(str(value))
        elif isinstance(value, str) and value.strip():
            facts[name] = value.strip()
    return facts


def _card_json(card: Card) -> str:
    return json.dumps({
        "id": card.listing_id,
        "title": card.title,
        "brand": card.brand,
        "price": f"{card.price} {card.currency}" if card.price is not None else None,
        "in_stock": card.in_stock,
        "rating": str(card.rating) if card.rating is not None else None,
        "review_count": card.review_count,
        "attributes": {k: attribute_text(v) for k, v in card.attributes.items()},
        "missing": card.missing,
    }, ensure_ascii=False)


def numbers_in(text: str) -> set[Decimal]:
    return {n for raw in SUMMARY_NUMBER.findall(text) if (n := to_decimal(raw)) is not None}


def check_claim(facts: dict[str, Decimal | str], field: str, value: str) -> tuple[str | None, Decimal | None]:
    """Return (error, number). The number is set when a numeric claim agrees with the card."""
    if field not in facts:
        return f"no field '{field}' on the card", None
    fact = facts[field]
    if isinstance(fact, str):
        return (None, None) if _as_text(value) == _as_text(fact) else (f"{field} is {fact}, not {value}", None)
    number = to_decimal(value)
    if number is None or number != fact:
        return f"{field} is {fact}, not {value}", None
    return None, number


def unchecked_numbers(text: str, allowed: set[Decimal], models: Sequence[str | None] = ()) -> list[str]:
    """Numbers in the text that are not allowed. Model numbers and the "/5" of a rating do not count."""
    text = RATING_SCALE.sub("", text)
    for model in models:
        if model:
            text = re.sub(re.escape(model), "", text, flags=re.IGNORECASE)
    return [raw for raw in SUMMARY_NUMBER.findall(text) if (n := to_decimal(raw)) is not None and n not in allowed]


def attribute_text(entry: dict) -> str:
    text = f"{entry.get('value')} {entry.get('unit') or ''}".strip()
    return f"{text} (doubtful)" if entry.get("doubtful") else text


def number_facts(facts: dict[str, Decimal | str]) -> str:
    return ", ".join(f"{k}={v.normalize():f}" for k, v in facts.items() if isinstance(v, Decimal)) or "none"


def passed_picks(text: PicksText, errors: list[str]) -> PicksText | None:
    """Keep the picks with no problem. Each problem of one pick starts with "<listing_id>:"."""
    failed = {int(head) for e in errors if (head := e.split(":", 1)[0]).isdigit()}
    kept = [p for p in text.picks if p.listing_id not in failed]
    return PicksText(picks=kept) if kept else None


def check_picks(text: PicksText, cards: dict[int, Card], conditions_text: str = "") -> list[str]:
    errors = []
    given = numbers_in(conditions_text)
    for pick in text.picks:
        card = cards.get(pick.listing_id)
        if card is None:
            errors.append(f"unknown listing_id {pick.listing_id}")
            continue
        facts = card_facts(card)
        claimed: set[Decimal] = set()
        for claim in pick.claims:
            error, number = check_claim(facts, claim.field, claim.value)
            if error:
                errors.append(f"{pick.listing_id}: {error}")
            elif number is not None:
                claimed.add(number)
        for raw in unchecked_numbers(pick.summary, claimed | given, [card.model]):
            errors.append(f"{pick.listing_id}: the number {raw} in the summary is not in the claims. Add a claim "
                          f"with the card field that has it, or remove it. Numbers on this card: {number_facts(facts)}")
    missing_ids = sorted(set(cards) - {p.listing_id for p in text.picks})
    if missing_ids:
        errors.append(f"no pick written for ids {missing_ids}")
    return errors


def write_picks(llm, cards: list[Card], conditions_text: str, language: str = "English",
                max_rounds: int = 3) -> tuple[PicksText | None, list[str]]:
    """Return the checked texts and the problems that are left. A pick with a problem has no text."""
    by_id = {card.listing_id: card for card in cards}
    base = f"Conditions: {conditions_text or 'none'}\n\nCards:\n" + "\n".join(_card_json(c) for c in cards)
    text, errors = None, []
    for _ in range(max_rounds):
        text = llm.structured("chat", SYSTEM.format(language=language), retry_prompt(base, text, errors), PicksText)
        errors = check_picks(text, by_id, conditions_text)
        llm.mark_checked(not errors, errors)
        if not errors:
            return text, []
    return passed_picks(text, errors), errors


def format_picks(picks: list[Card], texts: PicksText | None, notes: list[str],
                 conditions_text: str, dropped: int, check_errors: list[str] | None = None) -> str:
    lines = [f"Conditions: {conditions_text or 'none'}", ""]
    summaries = {p.listing_id: p.summary for p in texts.picks} if texts else {}
    for n, card in enumerate(picks, 1):
        price = f"{card.price} {card.currency}" if card.price is not None else "price unknown"
        rating = f"{card.rating} ({card.review_count} ratings)" if card.rating is not None else "no ratings"
        lines.append(f"{n}. {card.title}")
        lines.append(f"   {price} | {rating} | rechecked {card.fetched_at:%Y-%m-%d %H:%M} UTC | id {card.listing_id}")
        attrs = ", ".join(f"{k}: {attribute_text(v)}" for k, v in card.attributes.items())
        if attrs:
            lines.append(f"   {attrs}")
        if card.missing:
            lines.append("   " + ", ".join(f"{name}: missing" for name in card.missing))
        if card.listing_id in summaries:
            lines.append(f"   {summaries[card.listing_id]}")
        lines.append(f"   {card.url}")
    lines.append("")
    lines.append(f"How these were ranked: rating adjusted for review count (Bayesian average, prior weight "
                 f"{PRIOR_WEIGHT}). Products with missing data on your conditions come last. Price only "
                 "checks the budget.")
    if dropped:
        lines.append(f"{dropped} products failed your conditions and are not shown.")
    if check_errors:
        lines.append("The written summaries did not pass the checks, so only the facts are shown." if texts is None
                     else "Some summaries did not pass the checks. Those picks show only the facts.")
        lines += [f"check failed: {e}" for e in check_errors]
    lines += notes
    return "\n".join(lines)
