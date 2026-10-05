from decimal import Decimal

from scout.agent.history import (COMPACT_AFTER, KEEP, Summary, assistant, compact, history_text, last_user_text,
                                 user_numbers)
from tests.fakes import FakeLLM


def user(text):
    return {"role": "user", "content": text}


def test_history_shows_the_summary_and_the_messages_after_it():
    state = {"summary": "Wants a fridge.", "summarized": 1,
             "messages": [user("old"), user("höjd 186"), assistant("x" * 3000)]}
    text = history_text(state)
    assert text.startswith("Summary of the earlier conversation: Wants a fridge.")
    assert "old" not in text and "Shopper: höjd 186" in text
    assert text.endswith("[...]") and len(text) < 2200


def test_user_numbers_and_last_user_text():
    state = {"messages": [user("bredd 60, höjd 1,8 m"), assistant("Budget 9000?"), user("9 000 kr")]}
    assert user_numbers(state) == {Decimal("60"), Decimal("1.8"), Decimal("9000")}
    assert last_user_text(state) == "9 000 kr"
    multiplied = {"messages": [user("budget 9k, or 1万, max 12 tusen; 5 kg, 60 kr")]}
    assert {Decimal("9000"), Decimal("10000"), Decimal("12000")} <= user_numbers(multiplied)
    assert Decimal("5000") not in user_numbers(multiplied) and Decimal("60000") not in user_numbers(multiplied)


def test_compact_waits_until_the_history_is_long():
    llm = FakeLLM([Summary(summary="Wants a quiet fridge.")])
    short = {"messages": [user(str(n)) for n in range(COMPACT_AFTER)]}
    assert compact(short, llm) == {} and llm.requests == []
    long = {"messages": [user(str(n)) for n in range(COMPACT_AFTER + 1)]}
    assert compact(long, llm) == {"summary": "Wants a quiet fridge.", "summarized": COMPACT_AFTER + 1 - KEEP}
