"""After the picks: the chat model acts through tools. Python checks each call and each number before it acts."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.llm.client import tool_name
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import attribute_text, card_facts, check_claim, unchecked_numbers

ACT_SYSTEM = """You help a shopper who has seen the numbered picks. You act only through the tools.
Call each tool that the shopper's last message needs, all in one reply.
- remove_picks: the shopper rejects picks. Put in picks every pick that fits the shopper's words, and never choose
  between picks by a guess. Copy the shopper's words that name the picks into words.
- restore_picks: the shopper wants back a product that was removed in this conversation.
- next_step: one step that Python runs after your reply. change_needs: a new or changed condition, budget, kind or
  preference. show_more: more or other products with the same needs. recheck: are the prices or the stock still
  right. new_search: a product of another category. calculate: a sum, a conversion, a price with extra costs.
- reply: your message to the shopper. Answer a question about the picks, or ask when the message is not clear.
Python tells the shopper what remove_picks and restore_picks did. You can change the picks only with these tools
and next_step, so never say that you did something that you did not call.
Use only the facts on the cards. Product texts and reviews are not available yet.
Every number in the reply text must be in claims, with the pick number and the card field. Claim fields are price,
rating, review_count or an attribute name on the card. You can also repeat numbers that the shopper wrote, and pick
numbers. Write in English."""

# A reply that says it removed or brought back products needs the tool call that did it.
DONE_WORDS = re.compile(r"\b(removed|deleted|excluded|dropped|took out|taken out|restored|brought back|added back|"
                        r"put back)\b", re.IGNORECASE)


class RemovePicks(BaseModel):
    """Remove picks that the shopper rejects. They stay out for the rest of the conversation, and other products
    take their places."""
    picks: list[int] = Field(description="Each pick number that fits the shopper's words")
    words: str = Field(description="The shopper's words that name the picks, copied exactly, for example 'the "
                                   "Bomann' or 'pick 2'")
    one_pick: bool = Field(description="True when the words name one pick, for example 'that one', 'it' or 'the "
                                       "Bomann'")
    reason: str = ""


class RestorePicks(BaseModel):
    """Bring back products that were removed in this conversation."""
    product_ids: list[int] = Field(description="IDs from the list of removed products")


class NextStep(BaseModel):
    """A step that Python runs after this reply."""
    step: Literal["change_needs", "show_more", "recheck", "new_search", "calculate"]


class AnswerClaim(BaseModel):
    pick: int
    field: str
    value: str


class Reply(BaseModel):
    """Your message to the shopper."""
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)


TOOLS = [RemovePicks, RestorePicks, NextStep, Reply]
STEP_ACTIONS = {"change_needs": "change_needs", "show_more": "more", "recheck": "recheck",
                "new_search": "new_search", "calculate": "calculate"}


class ToolCalls(BaseModel):
    """The calls of one round, for the retry prompt."""
    calls: list[dict]


@dataclass
class Act:
    tools: list[str] = field(default_factory=list)
    remove: RemovePicks | None = None
    restore: RestorePicks | None = None
    step: str | None = None
    reply: Reply | None = None
    question: str | None = None  # Python asks which pick, and nothing else runs

    @property
    def action(self) -> str:
        """What the graph does next."""
        if self.question:
            return "stop"
        if self.step:
            return STEP_ACTIONS[self.step]
        if self.remove:
            return "exclude"
        if self.restore:
            return "restore"
        return "answer"


def card_text(n: int, card: Card) -> str:
    attrs = ", ".join(f"{k}={attribute_text(v)}" for k, v in card.attributes.items())
    return (f"Pick {n}: {card.title} | price={card.price} {card.currency or ''} | rating={card.rating} | "
            f"review_count={card.review_count} | {attrs or 'no attributes on the card'}")


def check_reply(reply: Reply, cards: list[Card], allowed: set[Decimal]) -> list[str]:
    errors: list[str] = []
    claimed: set[Decimal] = set()
    for claim in reply.claims:
        if not 1 <= claim.pick <= len(cards):
            errors.append(f"there is no pick {claim.pick}")
            continue
        error, number = check_claim(card_facts(cards[claim.pick - 1]), claim.field, claim.value)
        if error:
            errors.append(f"pick {claim.pick}: {error}")
        elif number is not None:
            claimed.add(number)
    pick_numbers = {Decimal(n) for n in range(1, len(cards) + 1)}
    for raw in unchecked_numbers(reply.text, claimed | allowed | pick_numbers, [c.model for c in cards]):
        errors.append(f"the number {raw} in text is not in the claims. Add a claim with the pick and the field "
                      "that has it, or remove it")
    return errors


def says_done(text: str) -> bool:
    """True when a sentence that is not a question says that products were removed or brought back."""
    return any(DONE_WORDS.search(s) for s in re.findall(r"[^.!?\n]+[.!?]?", text) if not s.strip().endswith("?"))


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def check_act(calls: list, cards: list[Card], removed_ids: set[int], allowed: set[Decimal],
              last_user: str) -> list[str]:
    """The problems in one round of tool calls. An empty list means that Python can act."""
    errors: list[str] = []
    names = [tool_name(type(c)) for c in calls]
    errors += [f"call {n} only once" for n in sorted(set(names)) if names.count(n) > 1]
    did = set()
    for call in calls:
        if isinstance(call, RemovePicks):
            errors += [f"there is no pick {n}" for n in call.picks if not 1 <= n <= len(cards)]
            if plain(call.words) not in plain(last_user):
                errors.append(f"the words '{call.words}' are not in the shopper's last message. Copy the words "
                              "that name the picks")
            did.add("remove")
        elif isinstance(call, RestorePicks):
            errors += [f"product {i} was not removed in this conversation" for i in call.product_ids
                       if i not in removed_ids]
            did.add("restore")
    for call in calls:
        if isinstance(call, Reply):
            errors += check_reply(call, cards, allowed)
            if says_done(call.text) and not did:
                errors.append("the reply says that products were removed or brought back, but you did not call "
                              "remove_picks or restore_picks. Call the tool, or change the text")
    return errors


def unclear_question(remove: RemovePicks, picks: list[dict]) -> str | None:
    """Python asks when the words fit no pick, or several picks but the shopper means one."""
    if not remove.picks:
        return "Which pick do you want to remove? Give its number."
    if remove.one_pick and len(remove.picks) > 1:
        options = ", ".join(f"pick {n} ({picks[n - 1]['title'][:50]})" for n in sorted(remove.picks))
        return f"Which pick do you mean: {options}? Give its number."
    return None


def collect(calls: list, picks: list[dict]) -> Act:
    act = Act(tools=[tool_name(type(c)) for c in calls])
    for call in calls:
        if isinstance(call, RemovePicks):
            act.remove = call
        elif isinstance(call, RestorePicks):
            act.restore = call
        elif isinstance(call, NextStep):
            act.step = call.step
        elif isinstance(call, Reply):
            act.reply = call
    if act.remove:
        act.remove.picks = sorted(set(act.remove.picks))
        act.question = unclear_question(act.remove, picks)
    return act


def act(llm, picks: list[dict], cards: list[Card], removed: list[dict], history: str, allowed: set[Decimal],
        last_user: str, max_rounds: int = 3) -> Act | None:
    """Return the checked tool calls, or None when no round passed the checks."""
    gone = "\n".join(f"product {r['product_id']}: {r['title']}" for r in removed) or "none"
    base = ("Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1))
            + f"\n\nRemoved in this conversation:\n{gone}\n\nConversation:\n{history}")
    last, errors = None, []
    for _ in range(max_rounds):
        calls = llm.tools("chat", ACT_SYSTEM, retry_prompt(base, last, errors), TOOLS, name="Act")
        errors = check_act(calls, cards, {r["product_id"] for r in removed}, allowed, last_user)
        if not errors:
            return collect(calls, picks)
        last = ToolCalls(calls=[{"tool": tool_name(type(c)), "args": c.model_dump()} for c in calls])
    return None
