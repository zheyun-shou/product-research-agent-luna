from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import FindProducts, ReadReviews, SearchShop, build_agent_graph
from scout.config import ROOT
from scout.eval.hidden import (ShopperReply, Target, leaked_words, load_targets, pick_rank, run_target, summarize,
                               summary_text)
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS, PICKS, Shop

TARGET = Target(id="t", source_id="B1", category="fridge", kind="fridge_freezer",
                opening="A fridge with a freezer, at most 180 cm", facts=["At most 180 cm high.", "Budget 5000 SEK."],
                hidden_words=["Bomann", "KG 320"])
TO_PICKS = [[NEEDS, SearchShop(words="kyl och frys")], [FindProducts()], [ReadReviews(ids=[2, 1])], [PICKS]]


def asins(ids):
    return [f"B{i}" for i in ids]


def run(llm, target=TARGET, turns=4, seen=lambda t: True):
    graph = build_agent_graph(Shop().deps(llm), InMemorySaver())
    return run_target(target, graph, llm, asins, seen, turns)


def test_the_hidden_product_in_the_picks_and_its_place():
    record = run(FakeLLM(list(TO_PICKS)))
    assert record["found"] and record["rank"] == 2 and record["picks"] == ["B2", "B1"]
    assert record["measured"] and record["shopper_turns"] == 0
    assert record["totals"]["tool_calls"] == 4 and record["totals"]["product_pages"] == 4
    assert record["totals"]["review_pages"] == 6 and record["totals"]["model_calls"] == 4
    assert record["turn_max"]["calls"] == 4 and record["at_limit"] == []


def test_the_shopper_answers_a_question_from_the_facts():
    llm = FakeLLM(["How high can it be?", ShopperReply(text="At most 180 cm."), *TO_PICKS])
    record = run(llm)
    assert record["found"] and record["shopper_turns"] == 1
    role, prompt = llm.requests[1]
    assert role == "shopper" and "- At most 180 cm high." in prompt and "Assistant: How high can it be?" in prompt
    assert [m["text"] for m in record["conversation"]][:3] == [TARGET.opening, "How high can it be?",
                                                              "At most 180 cm."]
    assert "At most 180 cm." in llm.requests[2][1]  # the answer reaches Luna
    assert record["totals"]["model_calls"] == 5  # the shopper does not count
    assert record["totals"]["model_calls_by_role"] == {"chat": 5}


def test_a_run_where_the_shopper_names_the_product_is_not_measured():
    record = run(FakeLLM(["Which one do you want?", ShopperReply(text="The bomann one, please.")]))
    assert record["leaked"] == ["Bomann"] and not record["measured"] and not record["found"]


def test_the_run_ends_after_the_last_shopper_turn_without_picks():
    replies = ["Which kind?", ShopperReply(text="Any."), "Which size?", ShopperReply(text="Any.")]
    record = run(FakeLLM(replies), turns=1, seen=lambda t: False)
    assert len(record["turns"]) == 2 and record["shopper_turns"] == 1
    assert not record["found"] and record["rank"] is None and record["measured"]


def test_pick_rank_counts_the_other_listings_of_the_product():
    target = TARGET.model_copy(update={"also": ["B7"]})
    assert pick_rank(["B3", "B7"], target) == 2 and pick_rank(["B3"], target) is None


def test_leaked_words_are_whole_words_in_any_case():
    assert leaked_words(TARGET, "the kg 320 model") == ["KG 320"]
    assert leaked_words(TARGET, "Bomanns or KG 3200") == []


def record(found, rank, calls, seconds, measured=True, at_limit=()):
    return {"measured": measured, "found": found, "rank": rank, "seen": True, "error": None, "leaked": [],
            "at_limit": list(at_limit),
            "totals": {"tool_calls": calls, "product_pages": 8, "review_pages": 6, "web_pages": 0, "web_searches": 0,
                       "model_calls": calls + 1, "cost_usd": 0.01, "seconds": seconds, "fact_problems": 0},
            "turn_max": {"calls": calls, "product_pages": 8, "review_pages": 6, "web_pages": 0, "web_searches": 0}}


def test_the_summary_gives_the_most_of_one_turn_in_the_runs_that_found_the_product():
    summary = summarize([record(True, 1, 10, 100), record(True, 3, 14, 200), record(False, None, 25, 400,
                                                                                   at_limit=["calls"])])
    assert summary["found"] == 2 and summary["seen_not_picked"] == 1 and summary["ranks"] == {"1": 1, "3": 1}
    assert summary["turn_max_found"]["calls"] == 14 and summary["turn_max_all"]["calls"] == 25
    assert summary["at_limit"]["calls"] == 1
    assert summary["totals"]["seconds"] == {"median": 200, "max": 400}
    assert "found in the picks 2" in summary_text(summary)


def test_the_targets_file_is_valid_and_never_names_the_products():
    targets = load_targets(ROOT / "eval" / "hidden" / "targets.yaml")
    assert len({t.id for t in targets}) == len(targets) >= 10
    for target in targets:
        assert leaked_words(target, " ".join([target.opening, *target.facts])) == [], target.id


@pytest.mark.db
def test_empty_tables_only_in_a_test_or_eval_database(db):
    from scout.db.conn import empty_tables
    db.execute("INSERT INTO fetch_log (source, url, status) VALUES ('a', 'u', 'ok')")
    empty_tables(db)
    assert db.execute("SELECT count(*) AS n FROM fetch_log").fetchone()["n"] == 0


def test_empty_tables_refuses_the_main_database():
    from scout.db.conn import empty_tables

    class Info:
        dbname = "product_scout"

    class Conn:
        info = Info()

    with pytest.raises(ValueError, match="will not empty"):
        empty_tables(Conn())
