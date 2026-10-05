"""What the chat model sees of the conversation: a summary of the old turns and the recent messages."""
import re
from decimal import Decimal

from pydantic import BaseModel

from scout.picks.write import numbers_in
from scout.sources.amazon_se.numbers import to_decimal

KEEP = 12            # messages that stay in full after a summary
COMPACT_AFTER = 20   # messages after the last summary that start a new summary
MAX_CHARS = 2000     # a long message, such as the picks, is cut in the prompt

SUMMARY_SYSTEM = """You summarize a shopping conversation for a later step of the same assistant.
Keep the needs of the shopper, the products that they rejected and why, and their open questions.
Do not add facts. Write at most 120 words, in English."""


class Summary(BaseModel):
    summary: str


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def history_text(state: dict) -> str:
    lines = []
    if state.get("summary"):
        lines.append(f"Summary of the earlier conversation: {state['summary']}")
    for message in state.get("messages", [])[state.get("summarized", 0):]:
        who = "Shopper" if message["role"] == "user" else "Assistant"
        text = message["content"]
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + " [...]"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def last_user_text(state: dict) -> str:
    return next((m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"), "")


# "9k", "9 tusen", "9千" and "1万" are written numbers too.
MULTIPLIED = re.compile(r"(\d+(?:[.,]\d+)?)\s*(k(?![a-zA-Z])|K(?![a-zA-Z])|tusen|thousand|千|万)")
MULTIPLIERS = {"k": 1000, "K": 1000, "tusen": 1000, "thousand": 1000, "千": 1000, "万": 10000}


def written_numbers(text: str) -> set[Decimal]:
    numbers = numbers_in(text)
    for raw, word in MULTIPLIED.findall(text):
        if (number := to_decimal(raw)) is not None:
            numbers.add(number * MULTIPLIERS[word])
    return numbers


def user_numbers(state: dict) -> set[Decimal]:
    """All numbers that the shopper wrote in this thread. Checks of later steps allow them."""
    numbers: set[Decimal] = set()
    for message in state.get("messages", []):
        if message["role"] == "user":
            numbers |= written_numbers(message["content"])
    return numbers


def compact(state: dict, llm) -> dict:
    messages, done = state.get("messages", []), state.get("summarized", 0)
    if len(messages) - done <= COMPACT_AFTER:
        return {}
    upto = len(messages) - KEEP
    old = history_text({"summary": state.get("summary", ""), "messages": messages[:upto], "summarized": done})
    reply = llm.structured("chat", SUMMARY_SYSTEM, old, Summary)
    return {"summary": reply.summary, "summarized": upto}
