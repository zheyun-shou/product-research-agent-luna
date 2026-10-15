from scout.needs import ConditionSpec, Needs
from scout.profiles import load_profile
from scout.search.filters import number_filters, off_condition, title_values
from scout.sources.base import Candidate, FilterGroup, FilterOption

PROFILE = load_profile("fridge")


def steps(key, name, unit, *ranges):
    return FilterGroup(key=key, name=name, options=[
        FilterOption(name=f"{lo}-{hi}", value=f"{key}:{n}", low=lo, high=hi, unit=unit)
        for n, (lo, hi) in enumerate(ranges)])


GROUPS = [steps("w", "Width", "cm", (None, 47.9), (48, 60.9), (61, 73.9), (74, None)),
          steps("h", "Height", "cm", (None, 60.9), (61, 85.9), (86, 111.9), (112, None)),
          steps("v", "Capacity", "L", (None, 89.9), (90, 169.9), (170, None)),
          FilterGroup(key="c", name="Colour", options=[FilterOption(name="Red", value="c:1")])]


def needs(*conds):
    return Needs(category="fridge", conditions=[ConditionSpec(attribute=a, op=o, value=v) for a, o, v in conds])


def test_the_steps_that_overlap_the_allowed_range():
    # 61.5 cm is in the step 61 to 73.9, so that step stays in for a width of at most 62 cm
    assert number_filters(needs(("width", "<=", 62)), PROFILE, GROUPS) == ["w:0", "w:1", "w:2"]
    assert number_filters(needs(("height", "<=", 86)), PROFILE, GROUPS) == ["h:0", "h:1", "h:2"]
    assert number_filters(needs(("height", ">=", 170), ("height", "<=", 180)), PROFILE, GROUPS) == ["h:3"]
    # Capacity is the site name of total_volume; the units L and l are the same
    assert number_filters(needs(("total_volume", ">=", 200)), PROFILE, GROUPS) == ["v:2"]


def test_no_filter_when_all_steps_or_none_fit_or_there_is_no_group():
    assert number_filters(needs(("width", "<=", 200)), PROFILE, GROUPS) == []
    assert number_filters(needs(("width", "<=", 40), ("width", ">=", 45)), PROFILE, GROUPS) == []
    assert number_filters(needs(("depth", "<=", 60)), PROFILE, GROUPS) == []
    assert number_filters(needs(("noise", "<=", 40)), PROFILE, GROUPS) == []


def test_the_sizes_that_a_title_gives():
    assert title_values("Gorenje RK 4182 PS4 Fridge/Freezer Combination/Height 180 cm/Cooling 198 l", PROFILE) == \
        {"height": 180.0}
    assert title_values("Kaiser KS 80425, 83 cm Wide, 183 cm High, 83.5 cm Deep", PROFILE) == \
        {"width": 83.0, "height": 183.0, "depth": 83.5}
    assert title_values("CANDY CCT3L517EW, kylskåpskombination, 55 cm, höjd 1,77 m", PROFILE) == {"height": 177.0}
    assert title_values("Beko TSE1284N Stand Kühlschrank, 101 l, 54 cm breit", PROFILE) == {"width": 54.0}
    # A niche height is not the height of the product, and sizes without words have no known order
    assert title_values("Bosch KIS86AFE0 Einbau, Nischenhöhe: 177.5 cm", PROFILE) == {}
    assert title_values("Amica FrostControl, 144 x 47.5 x 49.5 cm", PROFILE) == {}
    assert title_values("Fridge 180 cm high, height 185 cm", PROFILE) == {}  # two values: unknown


def test_a_card_outside_a_condition_is_left_out_and_an_unknown_value_stays():
    def card(title):
        return Candidate(source="s", source_id="1", url="u", title=title)
    wanted = needs(("height", "<=", 180), ("width", "<=", 56))
    assert off_condition(card("Hisense RB440N4AFA Refrigerator/Height 201 cm"), wanted, PROFILE)
    assert off_condition(card("Bomann SBS 7344 Side-by-Side, 90 cm wide"), wanted, PROFILE)
    assert not off_condition(card("Gorenje RK 4182 Height 180 cm, 55.6 cm wide"), wanted, PROFILE)
    assert not off_condition(card("Severin RKG 8930 Retro Kühl-Gefrier Combination"), wanted, PROFILE)
