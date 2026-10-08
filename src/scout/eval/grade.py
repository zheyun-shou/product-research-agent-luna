"""The grader: a second model scores each answer of Luna against a rubric.

Luna writes her answers freely. The grader checks them against what she saw in the conversation: the context of
the turn and the results of her tool calls. It checks what Python does not: are the facts in these sources, is the
arithmetic right, does the answer do what it says, is it honest, does it answer the question, is it clear.
"""
import json
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

Result = Literal["pass", "fail", "not_applicable"]

SYSTEM = """You grade one turn of a shopping assistant. The assistant helps a shopper choose products. It works with
tools: it searches shops, reads product pages, reviews and web pages, and Python runs the tools. You get the
sources: everything that the assistant saw (the context of the turn and the results of its tool calls). You get
the tool calls of this turn, and the messages to the shopper.

A list of picks is written by two authors. Python writes the title, the line with the price, the rating and the id,
the line of attributes and the address, all from the stored product. The model writes the text under each pick and
the note at the end. Messages marked [Python] are fixed texts of the program.

Grade the messages of this turn on each criterion. First write the reason, then the result.
The result is pass, fail or not_applicable (the criterion has nothing to grade in this turn).

- answers: the messages answer what the shopper asked in the last message, or ask one clear question when the
  message is not clear. Fail when they answer another question, or do something that the shopper did not ask for.
- facts: each fact about a product is in the sources, or in the shopper's words. A number can be rounded (166 cm
  for 166.5 cm). A test result or a forum view must be in the results of a web search, and the message must name
  the site. Fail when a fact is not in the sources, differs from them, or belongs to another product. In the reason,
  name each fact that fails.
- arithmetic: each calculated number and each comparison ("half", "twice", "more than", "fits") is correct. It
  uses facts of the sources, the numbers that the shopper gave, and for a currency conversion a plausible current
  rate that the message states. Fail when a number is wrong, when a conversion does not state its rate, or when
  the assistant added shipping, duty or moms that the shopper did not give. not_applicable when there is no
  calculation or comparison.
- said_did: the messages state or promise no action that the tool calls of this turn did not do. The list of tool
  calls is complete, and nothing runs after the turn. So "I'll recheck" or "I'll look for more" with no such tool
  call is a fail. Texts that Python wrote about its own actions pass.
- honest: the messages say when data is missing or in conflict, and invent no reviews, product texts, stock,
  tests or dates.
- plain: the messages are short and clear, with no text that the shopper did not need. Any language is fine.
  Fail when the model thanks, apologises or explains at length, or repeats itself. The fixed texts of Python are
  never too long.

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
FINAL_TOOLS = {"show_picks", "reply"}  # their arguments are the answer, not a source
MAX_ARGS = 400  # characters of the arguments of one tool call in the list of this turn


def turn_prompt(turn: dict, history: list[str]) -> str:
    """turn: say, replies, sources (texts), tools (the tool calls of this turn, as text) and model_texts (the
    messages that the model wrote; the others are Python texts or lists of picks)."""
    mine = set(turn.get("model_texts") or [])

    def author(reply: str) -> str:
        if reply in mine:
            return "Model"
        return "Picks" if "\n1. " in f"\n{reply}" else "Python"

    parts = ["Sources (what the assistant saw):\n" + ("\n\n".join(turn.get("sources") or []) or "none"),
             f"Conversation before this turn:\n{chr(10).join(history) or 'none'}",
             f"Shopper's last message:\n{turn['say']}",
             "Tool calls of this turn:\n" + ("\n".join(turn.get("tools") or []) or "none"),
             "Messages to the shopper in this turn:\n" + ("\n\n".join(
                 f"[{author(r)}] {r}" for r in turn.get("replies") or []) or "none")]
    return "\n\n".join(parts)


def grade_turn(llm, turn: dict, history: list[str]) -> Grade:
    return llm.structured("judge", SYSTEM, turn_prompt(turn, history), Grade)


def hidden_turns(run: dict) -> list[dict]:
    """The turns of one eval-hidden run in the form of turn_prompt. The sources of a turn are its context and the
    results of the tool calls of this turn and of the turns before."""
    evidence: list[str] = []
    items = []
    for turn in run.get("turns") or []:
        tools = turn.get("tools") or []
        evidence += [f"Result of {t['tool']} {t['args']}:\n{t['report']}" for t in tools
                     if t["tool"] not in FINAL_TOOLS]
        context = [f"Context at the start of the turn:\n{turn['context']}"] if turn.get("context") else []
        model = []
        for t in tools:
            if t["tool"] == "reply":
                try:
                    model.append(json.loads(t["args"]).get("text", ""))
                except ValueError:
                    pass
        items.append({"say": turn["say"], "replies": turn.get("replies") or [], "error": turn.get("error"),
                      "sources": context + list(evidence), "model_texts": model,
                      "tools": [f"{t['tool']} {t['args'][:MAX_ARGS]}" for t in tools]})
    return items


def grade_results(llm, results: dict, progress=lambda text: None) -> dict:
    """Add the grades to each turn of an eval-hidden results file, and their counts to the summary. Turns with an
    error or with no message get no grade."""
    counts = {name: Counter() for name in CRITERIA}
    for run in results["runs"]:
        lines: list[str] = []
        for number, (turn, item) in enumerate(zip(run.get("turns") or [], hidden_turns(run)), 1):
            if item["replies"] and not item["error"]:
                grade = grade_turn(llm, item, lines)
                turn["grades"] = {name: getattr(grade, name).model_dump() for name in CRITERIA}
                for name in CRITERIA:
                    counts[name][turn["grades"][name]["result"]] += 1
                failed = [name for name in CRITERIA if turn["grades"][name]["result"] == "fail"]
                progress(f"{run['target']} turn {number}: {', '.join(failed) or 'all pass'}")
            lines += [f"Shopper: {item['say']}"] + [f"Assistant: {r}" for r in item["replies"]]
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
