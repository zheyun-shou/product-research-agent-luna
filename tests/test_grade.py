import copy

import yaml

from scout.config import ROOT
from scout.eval.grade import (CRITERIA, CriterionGrade, Grade, check_grader, good_enough, grade_results, hidden_turns,
                              turn_prompt)
from tests.fakes import FakeLLM


def grade(**fails) -> Grade:
    return Grade(**{name: CriterionGrade(reason="r", result=fails.get(name, "pass")) for name in CRITERIA})


PICKS = "Conditions: height<=180\n\n1. Fridge 1\n   5000 SEK | id 1\n   Quiet."
RUN = {"target": "t", "turns": [
    {"say": "a fridge", "replies": ["Which height?"], "error": None, "context": "Conversation:\nShopper: a fridge",
     "tools": [{"tool": "reply", "args": '{"text": "Which height?"}', "report": "reply: the turn ends"}]},
    {"say": "at most 180 cm", "replies": [PICKS], "error": None,
     "context": "Conversation:\nShopper: a fridge\nAssistant: Which height?",
     "tools": [{"tool": "search_shop", "args": '{"words": "kylskåp"}', "report": "search_shop: id 1: Fridge 1 | "
                                                                               "height=170 cm"},
               {"tool": "show_picks", "args": '{"picks": [{"id": 1, "text": "Quiet."}]}', "report": ""}]},
    {"say": "ok", "replies": ["I cannot work on this now."], "error": "LLMUnavailable: busy", "context": "", "tools": []},
]}


def test_the_grader_sees_the_sources_the_history_and_the_tool_calls():
    items = hidden_turns(RUN)
    assert items[0]["model_texts"] == ["Which height?"]
    assert items[0]["sources"] == ["Context at the start of the turn:\nConversation:\nShopper: a fridge"]
    # The results of the tool calls of the turn are sources; the arguments of the final calls are not
    assert items[1]["sources"][1] == ('Result of search_shop {"words": "kylskåp"}:\nsearch_shop: id 1: Fridge 1 | '
                                      'height=170 cm')
    assert not any("show_picks" in source for source in items[1]["sources"])
    prompt = turn_prompt(items[1], ["Shopper: a fridge", "Assistant: Which height?"])
    assert "Conversation before this turn:\nShopper: a fridge\nAssistant: Which height?" in prompt
    assert 'Tool calls of this turn:\nsearch_shop {"words": "kylskåp"}\nshow_picks' in prompt
    assert "[Picks] Conditions: height<=180" in prompt
    assert "[Model] Which height?" in turn_prompt(items[0], [])
    assert "[Python] Removed." in turn_prompt({"say": "x", "replies": ["Removed."]}, [])


def test_the_results_of_the_turns_before_are_not_sources():
    # The chat model starts each turn with the context and the conversation only, so the grader must not see more
    run = copy.deepcopy(RUN)
    run["turns"][0]["tools"].insert(0, {"tool": "search_web", "args": '{"question": "test"}',
                                        "report": "search_web: the KG 320 is 44 dB"})
    items = hidden_turns(run)
    assert any("44 dB" in s for s in items[0]["sources"])
    assert not any("44 dB" in s for s in items[1]["sources"])


def test_only_turns_without_an_error_are_graded_and_counted():
    llm = FakeLLM([grade(), grade(said_did="fail", arithmetic="pass", plain="not_applicable")])
    results = grade_results(llm, {"summary": {}, "runs": [copy.deepcopy(RUN)]})
    assert [c.schema for c in llm.calls] == ["Grade", "Grade"] and llm.requests[0][0] == "judge"
    assert "Assistant: Which height?" in llm.requests[1][1]
    # A turn that was not graded is marked, so the grader does not pass a fact that repeats it
    again = copy.deepcopy(RUN)
    again["turns"].append({**RUN["turns"][1], "say": "and?"})
    llm = FakeLLM([grade(), grade(), grade()])
    grade_results(llm, {"summary": {}, "runs": [again]})
    assert "Assistant (not graded): I cannot work on this now." in llm.requests[2][1]
    turns = results["runs"][0]["turns"]
    assert turns[1]["grades"]["said_did"] == {"reason": "r", "result": "fail"} and "grades" not in turns[2]
    summary = results["summary"]["grades"]
    assert summary["judge"] == "fake-judge"
    assert summary["said_did"] == {"pass": 1, "fail": 1, "not_applicable": 0}
    assert summary["plain"] == {"pass": 1, "fail": 0, "not_applicable": 1}


def test_the_test_set_has_the_form_of_the_agent_loop():
    items = yaml.safe_load((ROOT / "eval" / "grader" / "known_faults.yaml").read_text(encoding="utf-8"))
    assert len({i["id"] for i in items}) == len(items) >= 40
    for item in items:
        assert item["sources"] and item["replies"] and set(item["expected"]) <= set(CRITERIA), item["id"]
        assert "cards" not in item and "actions" not in item, item["id"]


def test_check_grader_counts_found_faults_and_failed_correct_replies():
    items = [{"id": "a", "expected": {"facts": "fail"}}, {"id": "b", "expected": {"facts": "fail"}},
             {"id": "c", "expected": {"facts": "pass", "said_did": "pass"}}]
    rows = check_grader(items, [grade(facts="fail"), grade(), grade(said_did="fail")])
    assert rows["facts"] == {"faults": 2, "found": 1, "correct": 1, "failed": 0, "missed": ["b"],
                             "wrongly_failed": []}
    assert rows["said_did"] == {"faults": 0, "found": 0, "correct": 1, "failed": 1, "missed": [],
                                "wrongly_failed": ["c"]}
    assert not good_enough(rows["facts"]) and not good_enough(rows["said_did"])
    assert good_enough({"faults": 10, "found": 9, "correct": 10, "failed": 1})


def test_tools_that_did_not_run_are_marked_and_python_texts_are_not_graded():
    from scout.eval.grade import tool_line
    assert tool_line({"tool": "search_shop", "args": "{}", "report": "search_shop: not run, the tool calls of this "
                                                                        "turn are used up."}) == \
        "search_shop {} (not run: the tool calls of this turn are used up)"
    run = copy.deepcopy(RUN)
    run["turns"][0]["tools"] = []  # the model did not answer, so the reply is a Python text
    llm = FakeLLM([grade()])
    grade_results(llm, {"summary": {}, "runs": [run, {**copy.deepcopy(RUN), "leaked": ["Bomann"]}]})
    assert len(llm.calls) == 1  # only turn 2 of the first run


def test_an_old_results_file_is_refused():
    import pytest
    with pytest.raises(ValueError, match="run eval-hidden again"):
        grade_results(FakeLLM([]), {"summary": {}, "runs": [{"turns": [{"say": "x", "replies": ["y"]}]}]})
    with pytest.raises(ValueError, match="not an eval-hidden results file"):
        grade_results(FakeLLM([]), {"summary": {}, "turns": []})
