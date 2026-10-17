from datetime import datetime, timezone

from scout.extract.variants import size_number, size_page, variant_attributes
from scout.needs import ConditionSpec, Needs
from scout.profiles import load_profile
from scout.sources.base import ProductRecord

PROFILE = load_profile("running_shoes")
NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)


def test_size_numbers():
    assert size_number("38 EU", "EU") == 38.0
    assert size_number("38 2/3 EU", "EU") == 38.67
    assert size_number("43,5", "EU") == 43.5
    assert size_number("UK 5", "EU") is None and size_number("7 US", "EU") is None


def shoe():
    options = {"B01": {"size_name": "38 EU", "color_name": "Black"},
               "B02": {"size_name": "39 1/3 EU", "color_name": "Black"},
               "B03": {"size_name": "40 EU", "color_name": "Pink"},
               "B04": {"size_name": "37 1/3 EU", "color_name": "Black"}}
    return ProductRecord(source="amazon_se", source_id="B01", url="https://www.amazon.se/dp/B01", fetched_at=NOW,
                         options=options)


def test_the_sizes_with_the_colour_of_the_page_first():
    rec = shoe()
    rec.options["B05"] = {"size_name": "38 EU", "color_name": "Pink"}
    entry = variant_attributes(rec, PROFILE)["size"]
    assert entry["values"] == [37.33, 38.0, 39.33, 40.0]
    assert entry["value"] == "37 1/3 EU, 38 EU, 39 1/3 EU, 40 EU (only in Pink)"
    assert entry["pages"] == {"37.33": "B04", "38": "B01", "39.33": "B02", "40": "B03"}  # 38 EU in Black
    assert variant_attributes(shoe(), load_profile("fridge")) == {}


def needs(*conds):
    return Needs(category="running_shoes", conditions=[ConditionSpec(attribute="size", op=o, value=v)
                                                       for o, v in conds])


def test_the_page_of_the_size_of_the_needs():
    attributes = variant_attributes(shoe(), PROFILE)
    assert size_page(attributes, needs((">=", 39), ("<=", 39.5)), PROFILE) == ("39 1/3 EU", "B02")
    assert size_page(attributes, needs((">=", 40), ("<=", 40)), PROFILE) == ("40 EU (only in Pink)", "B03")
    assert size_page(attributes, needs((">=", 41), ("<=", 41)), PROFILE) == ("", "")
    assert size_page({}, needs((">=", 38), ("<=", 38)), PROFILE) == ("", "")  # no list of sizes
    assert size_page(attributes, needs(), PROFILE) is None
    assert size_page(attributes, needs((">=", 39.3333), ("<=", 39.3333)), PROFILE) == ("39 1/3 EU", "B02")
