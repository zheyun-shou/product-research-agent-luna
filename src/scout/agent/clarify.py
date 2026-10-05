"""Clarify the needs: compare what the shopper said with the category profile, and ask until the needs are clear."""
from decimal import Decimal

from pydantic import BaseModel

from scout.llm.client import LLMOutputError
from scout.llm.feedback import retry_prompt
from scout.needs import Needs, check_needs
from scout.profiles import CategoryProfile

SYSTEM = """You help a shopper say what they want, before a search on amazon.se.
You get the category profile, the needs so far and the conversation. Return the updated needs.
Rules:
- Use only what the shopper said. Never guess a size or a budget.
- kind is the name of one kind in the profile, or null. Choose a kind when the words of the shopper describe it,
  also without its name: "a fridge for my wine bottles" is a wine cooler. When two kinds fit, ask.
- conditions use only number attributes of the profile, in the unit of the profile.
  Change units when necessary, for example 1,8 m is 180 cm. "At most" is <=, "at least" is >=.
- budget is in SEK, or null. If the shopper gives a budget in another currency, ask for SEK.
- preferences are short notes of soft wishes, for example "quiet" or "white".
- search_terms_sv is null. Only when the shopper asks for words that the kind does not cover, it is one to four
  Swedish words for the amazon.se search.
- ready is true when the kind is known and the shopper answered or skipped the questions of the profile.
  When the shopper asks you to search now, ready is true if the kind is known.
- When ready is false, write one short message in question. It can ask about two or three things.
  Write it in English, also when the shopper writes in another language."""

CATEGORY_SYSTEM = """You find which product category a shopper wants.
Return the name of one category from the list, or null when none fits or the shopper did not say."""


class ClarifyReply(BaseModel):
    needs: Needs
    ready: bool
    question: str = ""


class CategoryChoice(BaseModel):
    category: str | None = None


def pick_category(llm, history: str, names: list[str]) -> str | None:
    prompt = f"Categories: {', '.join(names)}\n\nConversation:\n{history}"
    choice = llm.structured("chat", CATEGORY_SYSTEM, prompt, CategoryChoice)
    return choice.category if choice.category in names else None


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
        if not errors:
            return reply
    raise LLMOutputError(f"The needs were still not valid after {max_rounds} tries: {errors}")
