"""Numbers as amazon.se prints them: '2 499,00 kr', '4,5 av 5 stjärnor', '1 234 betyg'."""
import re
from decimal import Decimal, InvalidOperation

NUMBER = re.compile(r"\d[\d.,\u00a0\u202f ]*\d|\d")
_SPACES = re.compile(r"[\s\u00a0\u202f]+")
_RATING = re.compile(r"(\d(?:[.,]\d)?)\s*(?:av|out of)\s*5")
_COUNT = re.compile(r"(\d[\d.,\u00a0\u202f ]*\d|\d)\s*(tn|[kK])?(?!\w)")


def to_decimal(text: str) -> Decimal | None:
    """Read one number written with Swedish or English separators.

    Exactly three digits after the only separator means a thousands separator.
    """
    s = re.sub(r"[^\d,.]", "", _SPACES.sub("", text))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        sep = "," if "," in s else "." if "." in s else ""
        if sep:
            head, _, tail = s.rpartition(sep)
            head = head.replace(sep, "")
            s = head + tail if len(tail) == 3 else f"{head}.{tail}"
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _currency(text: str) -> str | None:
    low = text.lower()
    if "kr" in low or "sek" in low:
        return "SEK"
    if "€" in low or "eur" in low:
        return "EUR"
    return None


def parse_price(text: str) -> tuple[Decimal | None, str | None]:
    """'2 499,00 kr' gives (Decimal('2499.00'), 'SEK'). A range gives no price."""
    groups = NUMBER.findall(text)
    if len(groups) != 1:
        return None, _currency(text)
    return to_decimal(groups[0]), _currency(text)


def parse_rating(text: str) -> Decimal | None:
    match = _RATING.search(text)
    return to_decimal(match.group(1)) if match else None


def parse_count(text: str) -> int | None:
    match = _COUNT.search(text)
    if not match:
        return None
    number, multiplier = match.group(1), match.group(2)
    if multiplier:
        value = to_decimal(number)
        return int(value * 1000) if value is not None else None
    return int(re.sub(r"\D", "", number))
