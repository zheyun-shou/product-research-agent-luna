"""Draft profile against preset profile (phase 4 Task 10). For each category, the chat model drafts a profile from
the opening of one target, as update_needs does for a new category. The conditions of each search case are written
again in the attributes of the draft by the chat model. Then the saved search pages of an eval-search run are
scored offline with each profile (scout.eval.replay): the same pages, the same Python checks, only the profile
differs. No page loads; about 1 model call for each draft and 1 for each target."""
from pydantic import BaseModel, Field

from scout.agent.clarify import draft_profile
from scout.eval.hidden import Target
from scout.needs import ConditionSpec
from scout.profiles import CategoryProfile

MAP_SYSTEM = """You rewrite the conditions of a shopper in the attributes of a category profile.

- Use only the names of the number attributes of the profile, with their units. Convert a value when the unit of
  the profile differs (for example 0.5 kg is 500 g).
- When no attribute of the profile means the same as a condition, put that condition in left_out.
- Do not add conditions."""


class MappedConditions(BaseModel):
    conditions: list[ConditionSpec] = Field(default_factory=list)
    left_out: list[str] = Field(default_factory=list, description="The conditions that the profile cannot express")


def profile_text(profile: CategoryProfile) -> str:
    return "\n".join(f"- {a.name} ({a.type}, unit {a.unit or 'none'}): {a.label}" for a in profile.attributes)


def draft_for(llm, target: Target, names: list[str]) -> CategoryProfile:
    """The draft that the chat model makes from the opening of the target, without the preset of its category."""
    return draft_profile(llm, target.opening, f"shopper: {target.opening}", [n for n in names if n != target.category])


def map_conditions(llm, conditions: list[ConditionSpec], preset: CategoryProfile,
                   draft: CategoryProfile) -> MappedConditions:
    if not conditions:
        return MappedConditions()
    labels = {a.name: a for a in preset.attributes}
    lines = [f"- {c.attribute} {c.op} {c.value} {labels[c.attribute].unit or ''} ({labels[c.attribute].label})"
             if c.attribute in labels else f"- {c.attribute} {c.op} {c.value}" for c in conditions]
    mapped = llm.structured("chat", MAP_SYSTEM, "Profile:\n" + profile_text(draft) + "\n\nConditions:\n"
                            + "\n".join(lines), MappedConditions)
    known = {a.name for a in draft.attributes if a.type == "number"}
    kept = [c for c in mapped.conditions if c.attribute in known]
    dropped = [f"{c.attribute} {c.op} {c.value}" for c in mapped.conditions if c.attribute not in known]
    return MappedConditions(conditions=kept, left_out=[*mapped.left_out, *dropped])


def with_conditions(target: Target, conditions: list[ConditionSpec]) -> Target:
    return target.model_copy(update={"search": target.search.model_copy(update={"conditions": conditions})})


def title_words(profile: CategoryProfile) -> int:
    """The number attributes that the title check can read (a unit and title words)."""
    return sum(1 for a in profile.attributes if a.type == "number" and a.title_words and a.unit)
