"""Clarify the needs: compare what the shopper said with the category profile, and ask until the needs are clear."""
import re
from decimal import Decimal

from pydantic import BaseModel, Field

from scout.llm.client import LLMOutputError
from scout.llm.feedback import retry_prompt
from scout.needs import Needs, check_needs
from scout.profiles import CategoryProfile, ProductKind, ProfileAttribute

SYSTEM = """You help a shopper say what they want, before a search on amazon.se.
You get the category profile, the needs so far and the conversation. Return the updated needs.
Rules:
- Use what the shopper said. Never guess a size (height, width, depth) or a budget: a wrong size means that the
  product does not fit. You can infer soft conditions, for example a total volume for a family of four, and say
  so in the question or the preferences.
- kind is the name of one kind in the profile, or null. Choose a kind when the words of the shopper describe it,
  also without its name: "a fridge for my wine bottles" is a wine cooler. When two kinds fit, ask.
- conditions use only number attributes of the profile, in the unit of the profile.
  Change units when necessary, for example 1,8 m is 180 cm. "At most" is <=, "at least" is >=.
- budget is in SEK, or null. If the shopper gives a budget in another currency, convert it with a current rate
  that you know, and write the amount, the currency and the rate in budget_note, for example "800 EUR at 11.5 SEK".
- preferences are short notes of soft wishes, for example "quiet" or "white".
- search_terms_sv is null. Only when the shopper asks for words that the kind does not cover, it is one to four
  Swedish words for the amazon.se search.
- ready is true when the kind is known and the shopper answered or skipped the questions of the profile.
  When the shopper asks you to search now, ready is true if the kind is known.
- When ready is false, write one short message in question. It can ask about two or three things.
  Write it in the shopper's language."""

CATEGORY_SYSTEM = """You find which product category a shopper wants.
Each category has kinds. A product that is one of the kinds belongs to the category, also when the shopper uses
other words, for example a wine cabinet is the wine cooler kind of the fridge category.
Return the name of one category from the list, or null when none fits or the shopper did not say."""


class ClarifyReply(BaseModel):
    needs: Needs
    ready: bool
    question: str = ""


class CategoryChoice(BaseModel):
    category: str | None = None
    new_category: str | None = Field(None, description="When no category fits but the shopper names a product: a "
                                                       "short English name of it, for example 'headphones'")


def pick_category(llm, history: str, profiles: list[CategoryProfile]) -> CategoryChoice:
    """The model sees the kinds of each category, so "a wine cabinet" finds the fridge category. A category that
    is not in the list counts as none."""
    names = [p.category for p in profiles]
    lines = [f"- {p.category} ({p.display_name}). Kinds: {'; '.join(k.label for k in p.kinds) or 'none'}"
             for p in profiles]
    prompt = "Categories:\n" + "\n".join(lines) + f"\n\nConversation:\n{history}"
    choice = llm.structured("chat", CATEGORY_SYSTEM, prompt, CategoryChoice)
    if choice.category not in names:
        choice.category = None
    if choice.category:
        choice.new_category = None
    return choice


DRAFT_SYSTEM = """You draft a category profile for a shopping assistant that searches amazon.se.
The profile says which kinds of products the category has, which facts matter when a shopper compares them, and
what to ask the shopper.
- category: a short lowercase English name with underscores, for example "headphones".
- kinds: two to six kinds that shoppers choose between. search_terms_sv are the Swedish words that a shopper
  types on amazon.se for that kind.
- attributes: four to eight facts that product pages state and shoppers compare. type is number, enum, boolean
  or text. A number has a unit. An enum has its values. kind is hard when a wrong value means that the product
  cannot be used (for example a size that must fit), else soft. Never use price, rating or reviews: the system
  has them already.
- questions_for_user: three to five short questions in English that help choose.
- complaint_types: the problems that buyers of this category often report."""


class DraftProfile(BaseModel):
    category: str
    display_name: str
    search_terms_sv: str
    kinds: list[ProductKind]
    attributes: list[ProfileAttribute]
    questions_for_user: list[str]
    complaint_types: list[str] = Field(default_factory=list)


def check_draft(draft: DraftProfile, names: list[str]) -> list[str]:
    errors = []
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", draft.category):
        errors.append("category must be lowercase English letters and underscores")
    if draft.category in names:
        errors.append(f"the category {draft.category} exists already")
    if not draft.kinds:
        errors.append("give at least one kind")
    if len(draft.attributes) < 2:
        errors.append("give at least two attributes")
    errors += [f"attribute {a.name} is a number, so it needs a unit" for a in draft.attributes
               if a.type == "number" and not a.unit]
    errors += [f"attribute {a.name}: the system has {a.name} already" for a in draft.attributes
               if a.name in {"price", "rating", "review_count"}]
    if len({a.name for a in draft.attributes}) != len(draft.attributes):
        errors.append("attribute names must be unique")
    if not draft.questions_for_user:
        errors.append("give at least one question")
    return errors


def draft_profile(llm, wanted: str, history: str, names: list[str], max_rounds: int = 3) -> CategoryProfile:
    """The chat model drafts a profile for a category that does not exist. Python checks its form."""
    base = f"The shopper wants: {wanted}\nCategories that exist: {', '.join(names)}\n\nConversation:\n{history}"
    draft, errors = None, []
    for _ in range(max_rounds):
        draft = llm.structured("chat", DRAFT_SYSTEM, retry_prompt(base, draft, errors), DraftProfile)
        errors = check_draft(draft, names)
        llm.mark_checked(not errors, errors)
        if not errors:
            return CategoryProfile(**draft.model_dump(), sources={"products": ["amazon_se"]}, draft=True)
    raise LLMOutputError(f"The draft profile was still not valid after {max_rounds} tries: {errors}")


def describe_profile(profile: CategoryProfile) -> str:
    lines = [f"Category: {profile.display_name}", "Kinds:"]
    lines += [f"- {k.name}: {k.label}" for k in profile.kinds]
    lines.append("Attributes:")
    for attr in profile.attributes:
        detail = attr.unit or (", ".join(attr.values) if attr.values else attr.type)
        lines.append(f"- {attr.name} ({attr.type}, {detail}, {attr.kind})")
    lines.append("Questions to cover:")
    lines += [f"- {q}" for q in profile.questions_for_user]
    return "\n".join(lines)


def clarify(llm, profile: CategoryProfile, needs: Needs, history: str, said: set[Decimal],
            max_rounds: int = 3) -> ClarifyReply:
    base = f"{describe_profile(profile)}\n\nNeeds so far: {needs.model_dump_json()}\n\nConversation:\n{history}"
    reply, errors = None, []
    for _ in range(max_rounds):
        reply = llm.structured("chat", SYSTEM, retry_prompt(base, reply, errors), ClarifyReply)
        reply.needs.category = profile.category
        errors = check_needs(reply.needs, profile, said)
        if reply.ready and profile.kinds and reply.needs.kind is None:
            errors.append("ready is true, but the kind is not known")
        if not reply.ready and not reply.question.strip():
            errors.append("ready is false, so question must not be empty")
        llm.mark_checked(not errors, errors)
        if not errors:
            return reply
    raise LLMOutputError(f"The needs were still not valid after {max_rounds} tries: {errors}")
