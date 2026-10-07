"""The hidden-target test (agent-loop design, section 10). A simulated shopper wants one real product of amazon.se.
The shopper knows only the facts of that product, not its name. Luna does not know which product it is. Python
measures if the product is in the picks, at which place, and what the conversation used. This is the idea of the
WebShop benchmark (Yao et al., 2022)."""
import re
import statistics
import time
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from scout.agent.loop import MAX_PRODUCT_PAGES, MAX_REVIEW_PAGES, MAX_TOOL_CALLS, MAX_WEB_PAGES, MAX_WEB_SEARCHES

MAX_SHOPPER_TURNS = 4  # the shopper messages after the first one; then the run ends without picks
# The per-turn counters of the agent loop (state "used"), and the limit of each
LIMITS = {"calls": MAX_TOOL_CALLS, "product_pages": MAX_PRODUCT_PAGES, "review_pages": MAX_REVIEW_PAGES,
          "web_pages": MAX_WEB_PAGES, "web_searches": MAX_WEB_SEARCHES}
MEASURES = ("tool_calls", "product_pages", "review_pages", "web_pages", "web_searches", "model_calls", "cost_usd",
            "seconds", "fact_problems")


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a wrong key in the YAML must fail, not pass silently

    id: str
    source_id: str = Field(description="The ASIN of the hidden product on amazon.se")
    also: list[str] = Field(default_factory=list, description="Other listings of the same product")
    category: str
    kind: str
    opening: str = Field(description="The first message of the shopper")
    facts: list[str] = Field(description="What the shopper knows and wants. All are true of the hidden product")
    hidden_words: list[str] = Field(description="The brand and the model: the shopper must never write them")
    note: str = ""

    def ids(self) -> set[str]:
        return {self.source_id, *self.also}


def load_targets(path: Path) -> list[Target]:
    return [Target.model_validate(t) for t in yaml.safe_load(path.read_text(encoding="utf-8"))]


def leaked_words(target: Target, text: str) -> list[str]:
    """The hidden words that the text has, as whole words and with any case."""
    return [w for w in target.hidden_words if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.IGNORECASE)]


class ShopperReply(BaseModel):
    text: str = Field(description="Your next message to the assistant")


SHOPPER = """You play a shopper in a test of a shopping assistant. You chat with the assistant. You want one
product. You do not know its name, brand or model. You know only the facts below.

Rules:
- Answer the questions of the assistant only from the facts. When the facts do not answer a question, say that it
  does not matter to you.
- Do not give a fact that the assistant did not ask for. When the assistant asks if it can search, or gives no
  question, tell it to search.
- Never write a brand, a model name, a model number or a product id.
- Write one to three short sentences, in the language of your first message.
- Do not ask for a product that you saw in the chat. You cannot see the shop."""


def shopper_prompt(target: Target, conversation: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"{'You' if who == 'shopper' else 'Assistant'}: {text}" for who, text in conversation)
    return ("Your facts:\n- " + "\n- ".join(target.facts) + f"\n\nThe chat so far:\n{lines}\n\n"
            "Write your next message.")


def pick_rank(pick_ids: list[str], target: Target) -> int | None:
    """The place of the hidden product in the picks (1 is the first), or None."""
    return next((n for n, i in enumerate(pick_ids, 1) if i in target.ids()), None)


def run_target(target: Target, graph, llm, source_ids: Callable[[list[int]], list[str]],
               seen: Callable[[Target], bool], max_shopper_turns: int = MAX_SHOPPER_TURNS,
               progress=lambda text: None, after_turn=lambda: None) -> dict:
    """One conversation from the opening message to the first picks. The shopper answers each reply that is not
    the picks. source_ids gives the ASIN of each listing id. seen tells if the hidden product was stored in the run.
    after_turn runs after each turn, for example to end the database transaction."""
    config = {"configurable": {"thread_id": f"hidden-{target.id}"}}
    conversation: list[tuple[str, str]] = []
    turns, error, leaked = [], None, []
    calls_before = len(llm.calls)
    text = target.opening
    for number in range(1, max_shopper_turns + 2):
        conversation.append(("shopper", text))
        before = len(graph.get_state(config).values.get("messages", []))
        started, turn_error = time.monotonic(), None
        try:
            for _ in graph.stream({"messages": [{"role": "user", "content": text}]}, config, stream_mode="updates"):
                pass
        except Exception as exc:  # report it, and end this target
            turn_error = f"{type(exc).__name__}: {exc}"
        after_turn()
        state = graph.get_state(config).values
        replies = [m["content"] for m in state.get("messages", [])[before + 1:]]
        used = dict(state.get("used") or {})
        turns.append({"say": text, "replies": replies, "used": used, "seconds": round(time.monotonic() - started, 1),
                      "at_limit": sorted(name for name, limit in LIMITS.items() if used.get(name, 0) >= limit),
                      "error": turn_error})
        conversation += [("assistant", r) for r in replies]
        picks = state.get("picks") or []
        progress(f"{target.id} turn {number}: {turns[-1]['seconds']} s, {used.get('calls', 0)} tool calls, "
                 f"{used.get('product_pages', 0)} product pages{', picks' if picks else ''}"
                 f"{', error' if turn_error else ''}")
        if turn_error:
            error = turn_error
            break
        if picks or number > max_shopper_turns:
            break
        try:
            text = llm.structured("shopper", SHOPPER, shopper_prompt(target, conversation), ShopperReply).text
        except Exception as exc:
            error = f"shopper: {type(exc).__name__}: {exc}"
            break
        leaked = leaked_words(target, text)
        if leaked:  # the shopper named the product, so the run says nothing about Luna
            conversation.append(("shopper", text))
            break
    state = graph.get_state(config).values
    pick_ids = source_ids([p["listing_id"] for p in state.get("picks") or []])
    calls = llm.calls[calls_before:]
    agent_calls = [c for c in calls if c.role != "shopper"]
    model_calls: dict[str, int] = {}
    for call in agent_calls:
        model_calls[call.role] = model_calls.get(call.role, 0) + 1
    rank = pick_rank(pick_ids, target)
    # The fact checks do not stop an answer; they are counted here (design section 8)
    answers = [c for c in agent_calls if c.checked is not None]
    return {
        "target": target.id, "source_id": target.source_id, "found": rank is not None, "rank": rank,
        "seen": seen(target), "picks": pick_ids, "shopper_turns": len(turns) - 1, "error": error,
        "leaked": leaked, "measured": error is None and not leaked,
        "totals": {
            "tool_calls": sum(t["used"].get("calls", 0) for t in turns),
            "product_pages": sum(t["used"].get("product_pages", 0) for t in turns),
            "review_pages": sum(t["used"].get("review_pages", 0) for t in turns),
            "web_pages": sum(t["used"].get("web_pages", 0) for t in turns),
            "web_searches": sum(t["used"].get("web_searches", 0) for t in turns),  # Tavily credits at most
            "model_calls": len(agent_calls), "model_calls_by_role": model_calls,
            "cost_usd": round(sum(c.cost or 0 for c in agent_calls), 6),
            "shopper_cost_usd": round(sum(c.cost or 0 for c in calls if c.role == "shopper"), 6),
            "seconds": round(sum(t["seconds"] for t in turns), 1),
            "fact_problems": sum(len(c.problems or []) for c in answers),
        },
        "answers": [{"passed": c.checked, "problems": list(c.problems or [])} for c in answers],
        "turn_max": {name: max((t["used"].get(name, 0) for t in turns), default=0) for name in LIMITS},
        "at_limit": sorted({name for t in turns for name in t["at_limit"]}),
        "conversation": [{"who": who, "text": text} for who, text in conversation],
        "turns": turns,
    }


def summarize(records: list[dict]) -> dict:
    """Found and place count only in measured runs. The limits of the loop are for one turn, so the summary gives the
    most that one turn used, in all runs and in the runs that found the product."""
    measured = [r for r in records if r["measured"]]
    found = [r for r in measured if r["found"]]

    def spread(rows, key):
        values = [r["totals"][key] for r in rows]
        return {"median": statistics.median(values), "max": max(values)} if values else {}

    return {
        "targets": len(records),
        "measured": len(measured),
        "found": len(found),
        "seen_not_picked": sum(1 for r in measured if r["seen"] and not r["found"]),
        "ranks": {str(n): sum(1 for r in found if r["rank"] == n) for n in sorted({r["rank"] for r in found})},
        "errors": sum(1 for r in records if r["error"]),
        "leaked": sum(1 for r in records if r["leaked"]),
        "at_limit": {name: sum(1 for r in records if name in r["at_limit"]) for name in LIMITS},
        "totals": {key: spread(records, key) for key in MEASURES if records},
        "turn_max_all": {name: max((r["turn_max"][name] for r in records), default=0) for name in LIMITS},
        "turn_max_found": {name: max((r["turn_max"][name] for r in found), default=0) for name in LIMITS},
    }


def summary_text(summary: dict) -> str:
    lines = [f"targets {summary['targets']}, measured {summary['measured']} (no error and no leaked name), "
             f"found in the picks {summary['found']}, stored but not picked {summary['seen_not_picked']}",
             "places of the found products: " + (", ".join(f"{place}: {n}" for place, n in summary["ranks"].items())
                                                 or "none"),
             f"runs with an error {summary['errors']}, runs where the shopper named the product {summary['leaked']}",
             "runs that reached a limit of one turn: " + ", ".join(f"{name} {n}"
                                                                    for name, n in summary["at_limit"].items()),
             "for each run (median, max):"]
    lines += [f"  {key}: {v['median']}, {v['max']}" for key, v in summary["totals"].items() if v]
    lines.append("the most in one turn, all runs: " + ", ".join(f"{k} {v}" for k, v in summary["turn_max_all"].items()))
    lines.append("the most in one turn, runs that found the product: "
                 + ", ".join(f"{k} {v}" for k, v in summary["turn_max_found"].items()))
    return "\n".join(lines)
