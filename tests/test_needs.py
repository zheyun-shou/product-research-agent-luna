from decimal import Decimal

from scout.needs import UNIT_FACTORS, ConditionSpec, Needs, check_needs, was_said
from scout.profiles import load_profile

FRIDGE = load_profile("fridge")


def needs(**fields):
    return Needs(category="fridge", **fields)


def test_text_is_the_same_as_in_the_picks_command():
    n = needs(conditions=[ConditionSpec(attribute="height", op="<=", value=186)], budget=9000, preferences=["quiet"])
    assert n.text() == "height<=186, budget 9000 SEK, prefers quiet"
    assert n.budget_sek() == Decimal("9000")
    assert needs().text() == ""


def test_wanted_and_search_terms_follow_the_kind():
    assert needs(kind="mini_fridge").wanted(FRIDGE) == "Mini fridge for a room or an office"
    assert needs().wanted(FRIDGE) == "Fridge"
    assert needs(kind="mini_fridge").search_terms(FRIDGE) == "minikylskåp"
    assert needs().search_terms(FRIDGE) == "kylskåp"
    assert needs(kind="mini_fridge", search_terms_sv="liten kyl").search_terms(FRIDGE) == "liten kyl"


def test_only_lengths_and_weights_allow_a_unit_change():
    said = {Decimal("1.8"), Decimal("600"), Decimal("60"), Decimal("2")}
    assert was_said(180, said, UNIT_FACTORS["cm"])      # 1,8 m
    assert was_said(60, said, UNIT_FACTORS["cm"])       # 600 mm
    assert not was_said(185, said, UNIT_FACTORS["cm"])
    assert not was_said(200, said)                      # "2 people" is not 200 l
    assert not was_said(6000, said)                     # "60 cm" is not a budget of 6000


def test_check_needs_finds_unknown_names_and_invented_numbers():
    n = needs(kind="freezer_room", budget=5000, conditions=[
        ConditionSpec(attribute="colour", op="<=", value=1),
        ConditionSpec(attribute="energy_class", op="<=", value=3),
        ConditionSpec(attribute="height", op="<=", value=186),
    ])
    errors = check_needs(n, FRIDGE, said={Decimal("186")})
    assert any("kind 'freezer_room'" in e for e in errors)
    assert any("'colour'" in e for e in errors)
    assert any("'energy_class' is not a number" in e for e in errors)
    assert any("budget 5000" in e for e in errors)
    assert not any("height" in e for e in errors)
    volume = needs(conditions=[ConditionSpec(attribute="total_volume", op=">=", value=200)])
    assert any("total_volume 200 is not a number the shopper wrote" in e
               for e in check_needs(volume, FRIDGE, said={Decimal("2")}))
    assert check_needs(n.model_copy(update={"kind": None, "conditions": [], "budget": None}), FRIDGE) == []
