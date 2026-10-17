from pathlib import Path

import pytest
from pydantic import ValidationError

from scout.profiles import CategoryProfile, load_profile, profile_names, save_draft


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


def test_a_saved_draft_loads_and_is_listed(tmp_path):
    fixed, drafts = tmp_path / "profiles", tmp_path / "drafts"
    fixed.mkdir()
    (fixed / "fridge.yaml").write_text((Path(__file__).parents[1] / "profiles" / "fridge.yaml").read_text(
        encoding="utf-8"), encoding="utf-8")
    draft = load_profile("fridge", fixed, drafts).model_copy(update={"category": "headphones", "draft": True})
    save_draft(draft, drafts)
    assert profile_names(fixed, drafts) == ["fridge", "headphones"]
    assert load_profile("headphones", fixed, drafts).draft is True
    assert load_profile("fridge", fixed, drafts).draft is False


@pytest.mark.parametrize("name", [p.stem for p in (Path(__file__).parents[1] / "profiles").glob("*.yaml")])
def test_each_preset_has_kinds_with_search_terms_and_units_for_numbers(name):
    profile = load_profile(name)
    assert profile.category == name and not profile.draft and profile.search_terms_sv
    assert profile.kinds and all(k.search_terms_sv for k in profile.kinds)
    assert all(a.unit for a in profile.attributes if a.type == "number")
    assert profile.questions_for_user and profile.complaint_types


def test_a_changed_copy_of_a_preset_comes_first(tmp_path):
    fixed, drafts = tmp_path / "profiles", tmp_path / "drafts"
    fixed.mkdir()
    (fixed / "fridge.yaml").write_text((Path(__file__).parents[1] / "profiles" / "fridge.yaml").read_text(
        encoding="utf-8"), encoding="utf-8")
    edited = load_profile("fridge", fixed, drafts).model_copy(update={"edited": True, "attributes": []})
    save_draft(edited, drafts)
    assert load_profile("fridge", fixed, drafts).edited and profile_names(fixed, drafts) == ["fridge"]
