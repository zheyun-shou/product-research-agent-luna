"""After the picks: the chat model acts through tools. Python checks each call and each number before it acts."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.llm.client import tool_name
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import attribute_text, card_facts, check_claim, numbers_in, unchecked_numbers

ACT_SYSTEM = """You help a shopper who has seen the numbered picks. You act only through the tools.
Call each tool that the shopper's last message needs, all in one reply.
- remove_picks: the shopper rejects picks. Put in picks every pick that fits the shopper's words, and never choose
  between picks by a guess. Copy the shopper's words that name the picks into words.
- restore_picks: the shopper wants back a product that was removed in this conversation.
- next_step: one step that Python runs after your reply. change_needs: a new or changed condition, budget, kind or
  preference. show_more: more or other products with the same needs. recheck: are the prices or the stock still
  right. new_search: a product of another category.
- reply: your message to the shopper. Answer a question about the picks, or ask when the message is not clear.
Python tells the shopper what remove_picks and restore_picks did. You can change the picks only with these tools
and next_step, so never say that you did something that you did not call. Never promise an action for later:
do it now with a tool, or say that you cannot do it.
Use only the facts on the cards. Product texts and reviews are not available yet.
You can calculate sums, differences, conversions and comparisons. Use only the facts on the cards and the costs
and rates that the shopper wrote. Never add shipping, duty, moms or an exchange rate that the shopper did not give.
Ask for it.
Every number in the reply text must be one of these: a fact of a card in claims, with the pick number and the card
field (price, rating, review_count or an attribute name on the card); a number in derived, with how you got it; a
number that the shopper wrote; a pick number. Write in English."""

# A reply that says it did an action, or that it will do one, needs the tool call that does it. Nothing runs after
# the reply, so a promise without the call is false. Each entry: the words, and the calls that do the action.
DONE = [
    (re.compile(r"\b(removed|deleted|excluded|took out|taken out)\b", re.I), {"remove_picks"}),
    (re.compile(r"\b(restored|brought back|added back|put back)\b", re.I), {"restore_picks"}),
    (re.compile(r"\bI(?:'ve| have)? (re-?checked|checked (again|the (current )?(prices?|stock)))\b", re.I),
     {"next_step:recheck"}),
]
PROMISE = re.compile(r"\b(I'?ll|I will|I'?m going to|I am going to|let me|I'?m now|I am now)\b(?P<rest>.*)", re.I)
WILL_DO = [
    (re.compile(r"\b(remove|delete|exclude|drop|take out)\b", re.I), {"remove_picks"}),
    (re.compile(r"\b(restore|bring back|add back|put back)\b", re.I), {"restore_picks"}),
    (re.compile(r"\b(re-?check(ing)?|check(ing)? (again|the (current )?(prices?|stock)))\b", re.I),
     {"next_step:recheck"}),
    (re.compile(r"\b(search(ing)?( for)?|look(ing)? for|find(ing)?|show(ing)?)( you)? (more|other|another|new|similar|"
                r"cheaper)\b", re.I), {"next_step:show_more", "next_step:new_search", "next_step:change_needs"}),
]
# Words that say the shopper means each pick that fits.
ALL_WORDS = re.compile(r"\b(all|both|every|each|these|those|them)\b|全部|所有|都|两个|三个|这些|它们", re.I)
NOT_DONE = re.compile(r"\b(can'?t|cannot|can not|unable|not able|won'?t|will not|didn'?t|did not|if you|would you|"
                      r"should I|want me to)\b", re.I)


class RemovePicks(BaseModel):
    """Remove picks that the shopper rejects. They stay out for the rest of the conversation, and other products
    take their places."""
    picks: list[int] = Field(description="Each pick number that fits the shopper's words")
    words: str = Field(description="The shopper's words that name the picks, copied exactly, for example 'the "
                                   "Bomann' or 'pick 2'")
    reason: str = ""


class RestorePicks(BaseModel):
    """Bring back products that were removed in this conversation."""
    product_ids: list[int] = Field(description="IDs from the list of removed products")


class NextStep(BaseModel):
    """A step that Python runs after this reply."""
    step: Literal["change_needs", "show_more", "recheck", "new_search"]


class AnswerClaim(BaseModel):
    pick: int
    field: str
    value: str


class Derived(BaseModel):
    value: str
    how: str = Field(description="How you got it, in words, for example 'pick 1 price plus the 499 SEK shipping "
                                 "that the shopper gave'")


class Reply(BaseModel):
    """Your message to the shopper."""
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    derived: list[Derived] = Field(default_factory=list, description="Each number that you calculated")


TOOLS = [RemovePicks, RestorePicks, NextStep, Reply]
STEP_ACTIONS = {"change_needs": "change_needs", "show_more": "more", "recheck": "recheck",
                "new_search": "new_search"}


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
    stock = {True: "yes", False: "no", None: "not known"}[card.in_stock]
    return (f"Pick {n}: {card.title} | price={card.price} {card.currency or ''} | in_stock={stock} | "
            f"rating={card.rating} | review_count={card.review_count} | {attrs or 'no attributes on the card'}")


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
    # Python does not check the arithmetic of derived numbers. The grader of the evaluation does.
    derived = set().union(*(numbers_in(d.value) for d in reply.derived))
    for raw in unchecked_numbers(reply.text, claimed | derived | allowed | pick_numbers, [c.model for c in cards]):
        errors.append(f"the number {raw} in text is not in the claims or in derived. For a fact of a card, add a "
                      "claim with the pick and the field. For a number that you calculated, add it to derived with "
                      "how you got it. Or remove it")
    return errors


def unbacked_actions(text: str, done: set[str]) -> list[str]:
    """The words of each statement in the reply that says an action was done, or will be done, when no call in
    done does it. Questions, offers and refusals do not count."""
    missing = []
    for sentence in re.findall(r"[^.!?\n]+[.!?]?", text):
        if sentence.strip().endswith("?") or NOT_DONE.search(sentence):
            continue
        checks = [(sentence, pattern, tools) for pattern, tools in DONE]
        if promise := PROMISE.search(sentence):
            checks += [(promise.group("rest"), pattern, tools) for pattern, tools in WILL_DO]
        for part, pattern, tools in checks:
            if (found := pattern.search(part)) and not tools & done:
                missing.append(found.group(0))
    return missing


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def check_act(calls: list, cards: list[Card], removed_ids: set[int], allowed: set[Decimal],
              last_user: str) -> list[str]:
    """The problems in one round of tool calls. An empty list means that Python can act."""
    errors: list[str] = []
    names = [tool_name(type(c)) for c in calls]
    errors += [f"call {n} only once" for n in sorted(set(names)) if names.count(n) > 1]
    done = set()
    for call in calls:
        if isinstance(call, RemovePicks):
            errors += [f"there is no pick {n}" for n in call.picks if not 1 <= n <= len(cards)]
            if plain(call.words) not in plain(last_user):
                errors.append(f"the words '{call.words}' are not in the shopper's last message. Copy the words "
                              "that name the picks")
        elif isinstance(call, RestorePicks):
            errors += [f"product {i} was not removed in this conversation" for i in call.product_ids
                       if i not in removed_ids]
        done.add(f"next_step:{call.step}" if isinstance(call, NextStep) else tool_name(type(call)))
    for call in calls:
        if isinstance(call, Reply):
            errors += check_reply(call, cards, allowed)
            for words in unbacked_actions(call.text, done):
                errors.append(f"the reply says '{words}', but no tool call in this reply does it. Nothing runs "
                              "after your reply. Call the tool in this reply, or change the text")
    return errors


def unclear_question(remove: RemovePicks, picks: list[dict], last_user: str) -> str | None:
    """Python asks when the words fit no pick, or when they fit several picks and the shopper did not give each
    number or say all. Models often remove all picks that fit "the red one"."""
    if not remove.picks:
        return "Which pick do you want to remove? Give its number."
    if len(remove.picks) < 2:
        return None
    named = {int(n) for n in re.findall(r"\d+", last_user)}
    if set(remove.picks) <= named or ALL_WORDS.search(last_user):
        return None
    options = ", ".join(f"pick {n} ({picks[n - 1]['title'][:50]})" for n in remove.picks)
    return f"Which do you mean, all of these or only one: {options}? Say all, or give the number."


def collect(calls: list, picks: list[dict], last_user: str = "") -> Act:
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
        act.question = unclear_question(act.remove, picks, last_user)
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
        llm.mark_checked(not errors, errors)
        if not errors:
            return collect(calls, picks, last_user)
        last = ToolCalls(calls=[{"tool": tool_name(type(c)), "args": c.model_dump()} for c in calls])
    return None
