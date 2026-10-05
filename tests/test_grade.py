from scout.eval.grade import CRITERIA, CriterionGrade, Grade, check_grader, good_enough, grade_results, turn_prompt
from tests.fakes import FakeLLM


def grade(**fails) -> Grade:
    return Grade(**{name: CriterionGrade(reason="r", result=fails.get(name, "pass")) for name in CRITERIA})


TURNS = [
    {"script": "s", "turn": 1, "say": "a fridge", "replies": ["Here are 5 picks."], "measured": True,
     "cards": [], "new_cards": ["Pick 1: Fridge 1 | price=5000 SEK"], "actions": None, "searched": True},
    {"script": "s", "turn": 2, "say": "pick 1 plus 499 shipping?", "replies": ["5499 SEK. I'll recheck the price."],
     "measured": True, "cards": ["Pick 1: Fridge 1 | price=5000 SEK"], "new_cards": [],
     "actions": {"action": "answer", "tools": ["reply"], "pick_numbers": []}, "searched": False},
    {"script": "s", "turn": 3, "say": "ok", "replies": ["I cannot finish this step now."], "measured": False},
]


def test_the_grader_sees_the_cards_the_history_and_the_actions():
    prompt = turn_prompt(TURNS[1], ["Shopper: a fridge", "Assistant: Here are 5 picks."])
    assert "Cards before this turn:\nPick 1: Fridge 1 | price=5000 SEK" in prompt
    assert "Conversation before this turn:\nShopper: a fridge\nAssistant: Here are 5 picks." in prompt
    assert "tools called: reply" in prompt and "search started: no" in prompt
    assert prompt.endswith("Messages to the shopper in this turn:\n5499 SEK. I'll recheck the price.")
    assert "Cards after this turn:\nPick 1" in turn_prompt(TURNS[0], [])


def test_only_measured_turns_are_graded_and_counted():
    llm = FakeLLM([grade(), grade(said_did="fail", arithmetic="pass", plain="not_applicable")])
    results = grade_results(llm, {"summary": {}, "turns": [dict(t) for t in TURNS]})
    assert [c.schema for c in llm.calls] == ["Grade", "Grade"] and llm.requests[0][0] == "judge"
    assert "Assistant: Here are 5 picks." in llm.requests[1][1]
    assert results["turns"][1]["grades"]["said_did"] == {"reason": "r", "result": "fail"}
    assert "grades" not in results["turns"][2]
    summary = results["summary"]["grades"]
    assert summary["judge"] == "fake-judge"
    assert summary["said_did"] == {"pass": 1, "fail": 1, "not_applicable": 0}
    assert summary["plain"] == {"pass": 1, "fail": 0, "not_applicable": 1}


def test_check_grader_counts_found_faults_and_failed_correct_replies():
    items = [{"id": "a", "expected": {"facts": "fail"}}, {"id": "b", "expected": {"facts": "fail"}},
             {"id": "c", "expected": {"facts": "pass", "said_did": "pass"}}]
    rows = check_grader(items, [grade(facts="fail"), grade(), grade(said_did="fail")])
    assert rows["facts"] == {"faults": 2, "found": 1, "correct": 1, "failed": 0, "missed": ["b"]}
    assert rows["said_did"] == {"faults": 0, "found": 0, "correct": 1, "failed": 1, "missed": []}
    assert not good_enough(rows["facts"]) and not good_enough(rows["said_did"])
    assert good_enough({"faults": 10, "found": 9, "correct": 10, "failed": 1})
