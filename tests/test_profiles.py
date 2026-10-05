import pytest
from pydantic import ValidationError

from scout.profiles import CategoryProfile, load_profile


def test_fridge_profile_loads():
    profile = load_profile("fridge")
    assert profile.attribute("height").unit == "cm"
    assert profile.attribute("height").kind == "hard"
    assert profile.attribute("energy_class").values[0] == "A"
    assert profile.attribute("colour") is None


def test_unknown_category_lists_what_exists():
    with pytest.raises(FileNotFoundError, match="fridge"):
        load_profile("spaceship")


def base(attributes):
    return {"category": "x", "display_name": "X", "attributes": attributes,
            "questions_for_user": [], "sources": {"products": ["amazon_se"]}}


def test_enum_needs_values():
    with pytest.raises(ValidationError):
        CategoryProfile.model_validate(base([{"name": "c", "label": "C", "type": "enum", "kind": "soft"}]))


def test_attribute_names_must_be_unique():
    attr = {"name": "h", "label": "H", "type": "number", "unit": "cm", "kind": "hard"}
    with pytest.raises(ValidationError):
        CategoryProfile.model_validate(base([attr, attr]))


def test_fridge_kinds_have_swedish_search_terms():
    profile = load_profile("fridge")
    assert profile.search_terms_sv == "kylskåp"
    assert profile.kind("fridge_freezer").search_terms_sv == "kyl och frys"
    assert profile.kind("nothing") is None and profile.kind(None) is None
