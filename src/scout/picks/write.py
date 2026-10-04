"""The chat model writes the picks; Python checks every id and number against the cards."""
import json
import re
from decimal import Decimal

from pydantic import BaseModel

from scout.picks.rank import PRIOR_WEIGHT, Card
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You write short product recommendations for a shopper.
You get the shopper's conditions and product cards. Each card has an id and facts.
Rules:
- Write one pick per card, in the order given, with the card's id as listing_id.
- summary: one or two sentences on why the product fits, and what to watch out for, such as missing data.
- Use only facts on the cards.
- Every number you write in a summary must also be in that pick's claims, with the card field it comes from.
  Claim fields are price, rating, review_count, or an attribute name on the card. Do not write any other numbers.
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


def card_facts(card: Card) -> dict[str, Decimal]:
    facts: dict[str, Decimal] = {}
    if card.price is not None:
        facts["price"] = Decimal(card.price)
    if card.rating is not None:
        facts["rating"] = Decimal(card.rating)
    if card.review_count is not None:
        facts["review_count"] = Decimal(card.review_count)
    for name, entry in card.attributes.items():
        value = entry.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts[name] = Decimal(str(value))
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
        "attributes": {k: f"{v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items()},
        "missing": card.missing,
    }, ensure_ascii=False)


def check_picks(text: PicksText, cards: dict[int, Card]) -> list[str]:
    errors = []
    for pick in text.picks:
        card = cards.get(pick.listing_id)
        if card is None:
            errors.append(f"unknown listing_id {pick.listing_id}")
            continue
        facts = card_facts(card)
        claimed: set[Decimal] = set()
        for claim in pick.claims:
            if claim.field not in facts:
                errors.append(f"{pick.listing_id}: no field '{claim.field}' on the card")
                continue
            value = to_decimal(claim.value)
            if value is None or value != facts[claim.field]:
                errors.append(f"{pick.listing_id}: {claim.field} is {facts[claim.field]}, not {claim.value}")
                continue
            claimed.add(value)
        summary = RATING_SCALE.sub("", pick.summary)
        if card.model:
            summary = re.sub(re.escape(card.model), "", summary, flags=re.IGNORECASE)
        for raw in SUMMARY_NUMBER.findall(summary):
            number = to_decimal(raw)
            if number is not None and number not in claimed:
                errors.append(f"{pick.listing_id}: the number {raw} in the summary is not in the claims")
    missing_ids = sorted(set(cards) - {p.listing_id for p in text.picks})
    if missing_ids:
        errors.append(f"no pick written for ids {missing_ids}")
    return errors


def write_picks(llm, cards: list[Card], conditions_text: str, language: str = "English",
                max_rounds: int = 3) -> tuple[PicksText | None, list[str]]:
    by_id = {card.listing_id: card for card in cards}
    base = f"Conditions: {conditions_text or 'none'}\n\nCards:\n" + "\n".join(_card_json(c) for c in cards)
    errors: list[str] = []
    for _ in range(max_rounds):
        prompt = base if not errors else base + "\n\nYour last answer had these problems. Fix them:\n" + \
            "\n".join(f"- {e}" for e in errors)
        text = llm.structured("chat", SYSTEM.format(language=language), prompt, PicksText)
        errors = check_picks(text, by_id)
        if not errors:
            return text, []
    return None, errors


def format_picks(picks: list[Card], texts: PicksText | None, notes: list[str],
                 conditions_text: str, dropped: int, check_errors: list[str] | None = None) -> str:
    lines = [f"Conditions: {conditions_text or 'none'}", ""]
    summaries = {p.listing_id: p.summary for p in texts.picks} if texts else {}
    for n, card in enumerate(picks, 1):
        price = f"{card.price} {card.currency}" if card.price is not None else "price unknown"
        rating = f"{card.rating} ({card.review_count} ratings)" if card.rating is not None else "no ratings"
        lines.append(f"{n}. {card.title}")
        lines.append(f"   {price} | {rating} | rechecked {card.fetched_at:%Y-%m-%d %H:%M} UTC | id {card.listing_id}")
        attrs = ", ".join(f"{k}: {v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items())
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
        lines.append("The written summaries did not pass the checks, so only the facts are shown.")
        lines += [f"check failed: {e}" for e in check_errors]
    lines += notes
    return "\n".join(lines)
