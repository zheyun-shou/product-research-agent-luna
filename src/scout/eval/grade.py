"""The grader: a second model scores each reply of the scripted conversations against a rubric.

Python checks what a rule can check (facts against the cards, actions against the tool calls). The grader checks
what a rule cannot: is the arithmetic right, does the reply answer the question, is it honest and clear.
"""
import json
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

Result = Literal["pass", "fail", "not_applicable"]

SYSTEM = """You grade one turn of a shopping assistant. The assistant helps a shopper choose products on amazon.se.
It sees only the numbered product cards. It acts through tools, and Python does the actions. Each message has
its author. "Model" messages are written by the assistant. "Python" messages are fixed texts of the program, for
example "Removed: ..." and the list of numbered picks; only the short summary of each pick in that list is written
by the model. This is an evaluation run: searches and rechecks use stored data, so prices and dates do not change
after a recheck.

Grade the assistant's messages of this turn on each criterion. First write the reason, then the result.
The result is pass, fail or not_applicable (the criterion has nothing to grade in this turn).

- answers: the messages answer what the shopper asked in the last message, or ask one clear question when the
  message is not clear. Fail when they answer another question, or do something that the shopper did not ask for.
- facts: each fact about a product agrees with the cards. Fail when a fact is not on the cards, or differs from
  them. Facts from the shopper's own words are allowed.
- arithmetic: each calculated number and each comparison ("half", "twice", "more than") is correct, and uses only facts of the cards and the costs and rates that
  the shopper gave. Fail when a number is wrong, or when the assistant added shipping, duty, moms or an exchange
  rate that the shopper did not give. not_applicable when there is no calculation.
- said_did: the messages state or promise no action that the actions of this turn did not do. The list of
  actions is complete, and nothing runs after the turn. So "I'll recheck" or "I'll look for more" with no such
  action is a fail. Messages that Python wrote about its own actions ("Removed: ...", "Searching ...") pass.
- honest: the messages say when data is missing, and invent no reviews, product texts, stock or dates.
- plain: the messages are short, clear and in English, with no text that the shopper did not need. Fail when
  the model thanks, apologises or explains at length, or repeats itself. Grade only the model messages and the
  summaries. The fixed texts of Python are never too long.

Be strict. One wrong number is a fail."""


class CriterionGrade(BaseModel):
    reason: str = Field(description="Why, in one or two sentences. Write it before the result")
    result: Result


class Grade(BaseModel):
    answers: CriterionGrade
    facts: CriterionGrade
    arithmetic: CriterionGrade
    said_did: CriterionGrade
    honest: CriterionGrade
    plain: CriterionGrade


CRITERIA = list(Grade.model_fields)


def model_texts(turn: dict) -> set[str]:
    """The messages of the turn that the model wrote: the reply text of the passed tool calls, or the question of
    the clarify step. A test item gives them in model_texts."""
    if "model_texts" in turn:
        return set(turn["model_texts"])
    texts = set()
    for r in turn.get("rounds") or []:
        if not r["passed"]:
            continue
        output = json.loads(r["output"]) if r["output"] else None
        if r["schema"] == "Act" and isinstance(output, list):
            texts |= {json.loads(args).get("text", "") for name, args in output if name == "reply"}
        elif r["schema"] == "ClarifyReply" and isinstance(output, dict):
            texts.add(output.get("question", ""))
    return texts - {""}


def turn_prompt(turn: dict, history: list[str]) -> str:
    cards = "\n".join(turn.get("cards") or []) or "no cards yet"
    new = "\n".join(turn.get("new_cards") or [])
    actions = turn.get("actions") or {}
    step = actions.get("action")
    done = [f"tools called: {', '.join(actions.get('tools') or []) or 'none'}",
            f"next step: {step if step and step not in ('answer', 'stop') else 'none (only the messages)'}",
            f"removed picks: {actions.get('pick_numbers') or 'none'}",
            f"search started: {'yes' if turn.get('searched') else 'no'}"]
    mine = model_texts(turn)
    parts = [f"Cards before this turn:\n{cards}"]
    if new:
        parts.append(f"Cards after this turn:\n{new}")
    parts += [f"Conversation before this turn:\n{chr(10).join(history) or 'none'}",
              f"Shopper's last message:\n{turn['say']}",
              "Actions that Python did in this turn:\n" + "\n".join(done),
              "Messages to the shopper in this turn:\n" + ("\n\n".join(
                  f"[{'Model' if r in mine else 'Python'}] {r}" for r in turn.get("replies") or []) or "none")]
    return "\n\n".join(parts)


def grade_turn(llm, turn: dict, history: list[str]) -> Grade:
    return llm.structured("judge", SYSTEM, turn_prompt(turn, history), Grade)


def grade_results(llm, results: dict, progress=lambda text: None) -> dict:
    """Add the grades to each measured turn, and their counts to the summary. Turns that were not measured, or
    that have no messages, get no grade."""
    history: dict[str, list[str]] = {}
    counts = {name: Counter() for name in CRITERIA}
    for turn in results["turns"]:
        lines = history.setdefault(turn["script"], [])
        if turn.get("measured") and turn.get("replies"):
            grade = grade_turn(llm, turn, lines)
            turn["grades"] = {name: getattr(grade, name).model_dump() for name in CRITERIA}
            for name in CRITERIA:
                counts[name][turn["grades"][name]["result"]] += 1
            failed = [name for name in CRITERIA if turn["grades"][name]["result"] == "fail"]
            progress(f"{turn['script']} turn {turn['turn']}: {', '.join(failed) or 'all pass'}")
        lines += [f"Shopper: {turn['say']}"] + [f"Assistant: {r}" for r in turn.get("replies") or []]
    results["summary"]["grades"] = {
        "judge": llm.model_for("judge"),
        **{name: {r: counts[name][r] for r in ("pass", "fail", "not_applicable")} for name in CRITERIA}}
    return results


def grades_text(grades: dict) -> str:
    lines = [f"Grader: {grades['judge']}"]
    for name in CRITERIA:
        c = grades[name]
        graded = c["pass"] + c["fail"]
        lines.append(f"  {name}: {c['pass']} of {graded} pass ({c['not_applicable']} not applicable)")
    return "\n".join(lines)


def check_grader(items: list[dict], grades: list[Grade]) -> dict:
    """Compare the grades with the known results of the test set. For each criterion: the planted faults that the
    grader found, and the correct replies that it failed."""
    out = {name: {"faults": 0, "found": 0, "correct": 0, "failed": 0, "missed": [], "wrongly_failed": []}
           for name in CRITERIA}
    for item, grade in zip(items, grades):
        for name, expected in item["expected"].items():
            got = getattr(grade, name).result
            row = out[name]
            if expected == "fail":
                row["faults"] += 1
                row["found"] += got == "fail"
                if got != "fail":
                    row["missed"].append(item["id"])
            elif expected == "pass":
                row["correct"] += 1
                row["failed"] += got == "fail"
                if got == "fail":
                    row["wrongly_failed"].append(item["id"])
    return out


def good_enough(row: dict) -> bool:
    """Decision 3 of the plan: at least 9 of 10 planted faults found, at most 1 of 10 correct replies failed."""
    return row["found"] >= 0.9 * row["faults"] and row["failed"] <= 0.1 * row["correct"]
