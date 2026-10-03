from datetime import datetime, timezone
from decimal import Decimal

from scout.extract.page import NamedValue, PageExtraction, enrich_record
from scout.extract.verify import verify_raw
from scout.profiles import load_profile
from scout.sources.base import ProductRecord
from tests.fakes import FakeLLM

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)
TEXT = "Bosch KGN39 kylskåp\nPris: 4\u00a0999,00 kr\nHöjd: 1780 mm\nEnergieffektivitetsklass: E"
PROFILE = load_profile("fridge")


def record(**changes):
    base = ProductRecord(source="amazon_se", source_id="B0AAAAAAA2", url="https://www.amazon.se/dp/B0AAAAAAA2",
                         fetched_at=NOW, title="Bosch KGN39 kylskåp", brand="Bosch", model="KGN39",
                         in_stock=True, text=TEXT)
    return base.model_copy(update=changes)


def reply(*values):
    return PageExtraction(values=[NamedValue(name=n, value=v, raw=r) for n, v, r in values])


def test_verify_raw_ignores_spacing_and_case():
    assert verify_raw("4 999,00 KR", "Pris: 4\u00a0999,00 kr")
    assert not verify_raw("35 dB", TEXT)
    assert not verify_raw("", TEXT)


def test_fills_missing_price_and_attributes_with_checked_raw_text():
    llm = FakeLLM([reply(("price", "4999.00", "4 999,00 kr"),
                         ("height", "178", "1780 mm"),
                         ("energy_class", "E", "Energieffektivitetsklass: E"))])
    rec, called = enrich_record(record(), PROFILE, llm)
    assert called
    assert (rec.price, rec.currency, rec.llm_fields) == (Decimal("4999.00"), "SEK", ["price"])
    assert rec.attributes["height"] == {"value": 178.0, "unit": "cm", "raw": "1780 mm",
                                        "source_url": "https://www.amazon.se/dp/B0AAAAAAA2"}
    assert rec.attributes["energy_class"]["value"] == "E"


def test_value_whose_raw_text_is_not_on_the_page_is_dropped():
    llm = FakeLLM([reply(("noise", "35", "35 dB"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "noise" not in rec.attributes


def test_price_that_differs_from_its_raw_text_is_dropped():
    llm = FakeLLM([reply(("price", "3999", "4 999,00 kr"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert rec.price is None and rec.llm_fields == []


def test_enum_value_outside_the_profile_is_dropped():
    llm = FakeLLM([reply(("energy_class", "A+++", "Energieffektivitetsklass: E"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "energy_class" not in rec.attributes


def test_bare_number_as_raw_text_is_dropped():
    # "178" is on the page inside "1780 mm", but a bare number does not show which field it belongs to.
    llm = FakeLLM([reply(("height", "178", "178"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "height" not in rec.attributes


def test_amazon_size_line_in_millimeter_is_converted_to_cm():
    text = TEXT + "\nProduktens mått: 650D x 600B x 1850H millimeter"
    llm = FakeLLM([reply(("height", "185", "650D x 600B x 1850H millimeter"),
                         ("width", "600", "650D x 600B x 1850H millimeter"),
                         ("depth", "650", "650D"))])
    rec, _ = enrich_record(record(text=text), PROFILE, llm)
    assert rec.attributes["height"]["value"] == 185.0
    assert "width" not in rec.attributes  # 600 mm is not 600 cm
    assert "depth" not in rec.attributes  # no unit in the raw text


def test_number_that_its_raw_text_does_not_show_is_dropped():
    llm = FakeLLM([reply(("height", "180", "Höjd: 1780 mm"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "height" not in rec.attributes


def test_old_scale_a_plus_is_not_class_a():
    llm = FakeLLM([reply(("energy_class", "A", "Energiklass A+"))])
    rec, _ = enrich_record(record(text=TEXT + "\nEnergiklass A+"), PROFILE, llm)
    assert "energy_class" not in rec.attributes


def test_bare_enum_letter_as_raw_text_is_dropped():
    llm = FakeLLM([reply(("energy_class", "E", "E"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "energy_class" not in rec.attributes


def test_model_that_its_raw_text_does_not_show_is_dropped():
    llm = FakeLLM([reply(("model", "KGN39VLEB", "Bosch KGN39 kylskåp"))])
    rec, _ = enrich_record(record(model=None), PROFILE, llm)
    assert rec.model is None and rec.llm_fields == []


def test_prompt_asks_only_for_what_is_missing_and_uses_the_worker():
    llm = FakeLLM([reply()])
    enrich_record(record(), PROFILE, llm)
    role, prompt = llm.requests[0]
    assert role == "worker"
    assert "- price:" in prompt and "- brand:" not in prompt
    assert "- height: Height, number in cm" in prompt


def test_no_call_when_nothing_is_missing():
    full = {a.name: {"value": 1} for a in PROFILE.attributes}
    llm = FakeLLM([])
    rec, called = enrich_record(record(price=Decimal("4999"), attributes=full), PROFILE, llm)
    assert not called and llm.requests == []
