"""The hidden-target test (agent-loop design, section 10). A simulated shopper wants one real product of amazon.se.
The shopper knows only the facts of that product, not its name. Luna does not know which product it is. Python
measures if the product is in the picks, at which place, and what the conversation used. This is the idea of the
WebShop benchmark (Yao et al., 2022)."""
import re
import statistics
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from scout.agent.loop import MAX_PRODUCT_PAGES, MAX_REVIEW_PAGES, MAX_TOOL_CALLS, MAX_WEB_PAGES, MAX_WEB_SEARCHES
from scout.needs import ConditionSpec

MAX_SHOPPER_TURNS = 4  # the shopper messages after the first one; then the run ends without picks
# The per-turn counters of the agent loop (state "used"), and the limit of each
LIMITS = {"calls": MAX_TOOL_CALLS, "product_pages": MAX_PRODUCT_PAGES, "review_pages": MAX_REVIEW_PAGES,
          "web_pages": MAX_WEB_PAGES, "web_searches": MAX_WEB_SEARCHES}
MEASURES = ("tool_calls", "product_pages", "review_pages", "web_pages", "web_searches", "web_credits", "model_calls",
            "cost_usd", "seconds", "gate_stops")
# The kinds of the problems that the gate stopped (loop.check_final), from the start of their text
STOP_KINDS = (("shop_floor", "no shop search"), ("reviews_floor", "the reviews of id"),
              ("web_floor", "had no web search"), ("removed", "was removed by the shopper"),
              ("no_pick", "show_picks has no pick"), ("no_product", "there is no product"))
# Where the hidden product stopped, from the end of the funnel to its start: in the picks; shown to Luna by
# find_products, search_text or similar_products, or read by her, but not picked; stored but never shown; in the
# results of a shop search but past the pages read; in the results but left out (over the budget, off topic by
# title, or the page did not load or parse); in no result of a shop search
STAGES = ("picked", "considered", "stored", "not_read", "left_out", "not_in_results")
LEFT_OUT = ("over_budget", "off_condition", "off_topic", "not_loaded", "not_parsed")


def turn_tools(messages: list[dict]) -> list[dict]:
    """The tool calls of one turn with their results, in order: what Luna saw. The grader checks the answer against
    them."""
    reports = {m.get("tool_call_id"): m.get("content") or "" for m in messages if m.get("role") == "tool"}
    return [{"tool": c["function"]["name"], "args": c["function"]["arguments"], "report": reports.get(c["id"], "")}
            for m in messages if m.get("role") == "assistant" for c in m.get("tool_calls") or []]


def stop_kind(problem: str) -> str:
    """The kind of one gate problem. A problem of one id that is none of the floors comes from the live check."""
    for kind, words in STOP_KINDS:
        if words in problem:
            return kind
    return "live_check" if problem.startswith("id ") else "other"


class SearchCase(BaseModel):
    """The search of a target for `scout eval-search`: fixed words and needs, so no model is called."""
    model_config = ConfigDict(extra="forbid")

    words: str = Field(description="Swedish search words, as Luna writes them")
    budget: float | None = Field(None, description="SEK")
    conditions: list[ConditionSpec] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list, description="Word filters as 'Group: Option'")


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
    search: SearchCase | None = None
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


def funnel_stage(target: Target, found: bool, seen: bool, turns: list[dict], search_log: list[dict],
                 listing_ids: list[int]) -> tuple[str, list[dict]]:
    """The stage of STAGES where the hidden product stopped, and what each shop search did with it. listing_ids are
    the stored listings of the hidden product and of the products joined with it."""
    searches = [{"words": s["words"], "page": s["page"], "outcome": s["outcomes"][i]}
                for s in search_log for i in sorted(target.ids()) if i in s["outcomes"]]
    if found:
        return "picked", searches
    for turn in turns:
        if (set(turn["used"].get("read") or []) | set(turn["used"].get("shown") or [])) & set(listing_ids):
            return "considered", searches
    outcomes = {s["outcome"] for s in searches}
    # A page that was read but stored under another ASIN (a redirect to a variant) counts as stored
    if seen or outcomes & {"read", "stored_before"}:
        return "stored", searches
    if "unread" in outcomes:
        return "not_read", searches
    return ("left_out" if outcomes & set(LEFT_OUT) else "not_in_results"), searches


def run_target(target: Target, graph, llm, source_ids: Callable[[list[int]], list[str]],
               seen: Callable[[Target], bool], max_shopper_turns: int = MAX_SHOPPER_TURNS,
               progress=lambda text: None, after_turn=lambda: None, search_log: list[dict] | None = None,
               listings: Callable[[Target], list[int]] = lambda target: []) -> dict:
    """One conversation from the opening message to the first picks. The shopper answers each reply that is not
    the picks. source_ids gives the ASIN of each listing id. seen tells if the hidden product was stored in the run.
    after_turn runs after each turn, for example to end the database transaction. search_log is the list that the
    shop searches of the run fill (loop_deps), and listings gives the stored listings of the hidden product: with
    them the record says where the product stopped (funnel_stage)."""
    config = {"configurable": {"thread_id": f"hidden-{target.id}"}}
    conversation: list[tuple[str, str]] = []
    turns, error, leaked = [], None, []
    calls_before = len(llm.calls)
    text = target.opening
    for number in range(1, max_shopper_turns + 2):
        conversation.append(("shopper", text))
        before = len(graph.get_state(config).values.get("messages", []))
        started, turn_error, where = time.monotonic(), None, None
        try:
            for _ in graph.stream({"messages": [{"role": "user", "content": text}]}, config, stream_mode="updates"):
                pass
        except Exception as exc:  # report it with the place in the code, and end this target
            turn_error = f"{type(exc).__name__}: {exc}"
            where = traceback.format_exc(limit=-3)
        after_turn()
        state = graph.get_state(config).values
        replies = [m["content"] for m in state.get("messages", [])[before + 1:]]
        used = dict(state.get("used") or {})
        turns.append({"say": text, "replies": replies, "used": used, "seconds": round(time.monotonic() - started, 1),
                      "context": state.get("turn_context") or "", "tools": turn_tools(state.get("turn_messages") or []),
                      "where": where,
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
    stored = seen(target)
    stage, searches = funnel_stage(target, rank is not None, stored, turns, search_log or [],
                                   listings(target) if stored else [])
    return {
        "target": target.id, "source_id": target.source_id, "found": rank is not None, "rank": rank,
        "seen": stored, "stage": stage, "target_searches": searches, "picks": pick_ids,
        "shopper_turns": len(turns) - 1, "error": error,
        "leaked": leaked, "measured": error is None and not leaked,
        "totals": {
            "tool_calls": sum(t["used"].get("calls", 0) for t in turns),
            "product_pages": sum(t["used"].get("product_pages", 0) for t in turns),
            "review_pages": sum(t["used"].get("review_pages", 0) for t in turns),
            "web_pages": sum(t["used"].get("web_pages", 0) for t in turns),
            "web_searches": sum(t["used"].get("web_searches", 0) for t in turns),
            "web_credits": sum(t["used"].get("web_credits", 0) for t in turns),  # Tavily; SearXNG is free
            "model_calls": len(agent_calls), "model_calls_by_role": model_calls,
            "cost_usd": round(sum(c.cost or 0 for c in agent_calls), 6),
            "shopper_cost_usd": round(sum(c.cost or 0 for c in calls if c.role == "shopper"), 6),
            "seconds": round(sum(t["seconds"] for t in turns), 1),
            "gate_stops": sum(len(t["used"].get("gate_stops", [])) for t in turns),
        },
        "gate_stops": [{"turn": n, "kind": stop_kind(p), "problem": p}
                       for n, t in enumerate(turns, 1) for p in t["used"].get("gate_stops", [])],
        "gate_gave_up": sum(t["used"].get("gate_gave_up", 0) for t in turns),
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
        values = [r["totals"].get(key, 0) for r in rows]  # results from before a measure existed have 0
        return {"median": statistics.median(values), "max": max(values)} if values else {}

    return {
        "targets": len(records),
        "measured": len(measured),
        "found": len(found),
        "seen_not_picked": sum(1 for r in measured if r["seen"] and not r["found"]),
        # results from before 2026-10-15 have no stage
        "stages": {s: n for s in STAGES if (n := sum(1 for r in measured if r.get("stage") == s))},
        "ranks": {str(n): sum(1 for r in found if r["rank"] == n) for n in sorted({r["rank"] for r in found})},
        "errors": sum(1 for r in records if r["error"]),
        "leaked": sum(1 for r in records if r["leaked"]),
        "at_limit": {name: sum(1 for r in records if name in r["at_limit"]) for name in LIMITS},
        "totals": {key: spread(records, key) for key in MEASURES if records},
        "turn_max_all": {name: max((r["turn_max"][name] for r in records), default=0) for name in LIMITS},
        "turn_max_found": {name: max((r["turn_max"][name] for r in found), default=0) for name in LIMITS},
        "gate_stops": dict(Counter(s["kind"] for r in records for s in r.get("gate_stops", []))),
        "gate_gave_up": sum(r.get("gate_gave_up", 0) for r in records),
    }


def summary_text(summary: dict) -> str:
    lines = [f"targets {summary['targets']}, measured {summary['measured']} (no error and no leaked name), "
             f"found in the picks {summary['found']}, stored but not picked {summary['seen_not_picked']}",
             "where the hidden product stopped: " + (", ".join(f"{s} {n}" for s, n in summary.get("stages", {}).items())
                                                     or "not recorded"),
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
    stops = summary.get("gate_stops") or {}
    lines.append("what the gate stopped: " + (", ".join(f"{k} {v}" for k, v in sorted(stops.items())) or "nothing")
                 + f"; turns given up {summary.get('gate_gave_up', 0)}")
    return "\n".join(lines)
