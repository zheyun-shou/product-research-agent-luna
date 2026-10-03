from decimal import Decimal

import pytest

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating, to_decimal


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2 499,00 kr", Decimal("2499.00")),
        ("8\u00a0990,00\u00a0kr", Decimal("8990.00")),
        ("1\u202f299,50 kr", Decimal("1299.50")),
        ("2.499,00 kr", Decimal("2499.00")),
        ("SEK 1,299.00", Decimal("1299.00")),
        ("499 kr", Decimal("499")),
    ],
)
def test_parse_price_reads_swedish_and_english_formats(text, expected):
    assert parse_price(text) == (expected, "SEK")


def test_parse_price_refuses_a_range():
    assert parse_price("2 499,00 kr - 2 999,00 kr") == (None, "SEK")


def test_parse_price_without_digits():
    assert parse_price("Se pris i kassan") == (None, None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("4,5 av 5 stjärnor", Decimal("4.5")),
        ("4.5 out of 5 stars", Decimal("4.5")),
        ("5,0 av 5 stjärnor", Decimal("5.0")),
        ("Inga betyg ännu", None),
    ],
)
def test_parse_rating(text, expected):
    assert parse_rating(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 234 betyg", 1234),
        ("1\u202f234 globala betyg", 1234),
        ("(1\u00a0234)", 1234),
        ("(1.2K)", 1200),
        ("1,2 tn", 1200),
        ("87", 87),
        ("inga", None),
    ],
)
def test_parse_count(text, expected):
    assert parse_count(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("4,5", Decimal("4.5")), ("1.234", Decimal("1234")), ("59.5", Decimal("59.5")), ("", None), ("abc", None)],
)
def test_to_decimal(text, expected):
    assert to_decimal(text) == expected
