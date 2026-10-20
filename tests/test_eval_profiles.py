from scout.eval.profiles import MappedConditions, map_conditions, title_words
from scout.needs import ConditionSpec
from scout.profiles import CategoryProfile, load_profile


class Mapper:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def structured(self, role, system, user, schema):
        self.prompts.append(user)
        return self.reply


def draft():
    preset = load_profile("headphones").model_dump()
    attrs = [{**a, "name": "battery_life_hours"} if a["name"] == "battery_life" else a for a in preset["attributes"]]
    return CategoryProfile(**{**preset, "attributes": attrs, "draft": True})


def test_the_conditions_are_written_in_the_attributes_of_the_draft_and_unknown_names_are_left_out():
    preset = load_profile("headphones")
    battery = next(a.name for a in preset.attributes if a.unit == "h")
    llm = Mapper(MappedConditions(conditions=[ConditionSpec(attribute="battery_life_hours", op=">=", value=30),
                                              ConditionSpec(attribute="made_up", op="<=", value=1)]))
    got = map_conditions(llm, [ConditionSpec(attribute=battery, op=">=", value=30)], preset, draft())
    assert [c.attribute for c in got.conditions] == ["battery_life_hours"]
    assert got.left_out == ["made_up <= 1.0"]
    assert f"- {battery} >= 30.0 h" in llm.prompts[0] and "battery_life_hours (number, unit h)" in llm.prompts[0]
    assert map_conditions(llm, [], preset, draft()) == MappedConditions() and len(llm.prompts) == 1


def test_title_words_counts_the_attributes_that_the_title_check_reads():
    assert title_words(load_profile("headphones")) >= 1 and title_words(load_profile("incense")) == 0
