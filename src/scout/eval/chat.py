"""Scripted conversations: the real chat model talks with the graph over stored products, and Python scores it."""
import statistics
import time
from collections import Counter
from pathlib import Path

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from scout.agent.graph import Deps, build_graph
from scout.agent.followup import card_text
from scout.agent.live import shared_deps
from scout.crawl import CrawlReport
from scout.needs import Needs
from scout.picks.rank import Card
from scout.sources.base import ProductRecord

# The steps whose output Python checks, by schema name. Each step marks its last call record as passed or not.
CHECKED = {"ClarifyReply": "clarify", "PicksText": "summaries", "Act": "actions"}

# A node caught an outage (quota, server) or a program error. The model is then not measured in this turn.
NODE_ERRORS = ("I cannot finish this step now", "Something went wrong in the step")
PICKS_DIFFER = "The picks of an earlier turn differ from the script, so the references to them cannot be scored."


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a wrong key in the YAML must fail, not pass silently

    phase: str | None = None
    category: str | None = None             # "none": the agent must not choose a category
    kind: str | None = None
    budget: float | None = None
    no_budget: bool = False
    conditions: list[str] | None = None     # for example ["height<=186"]; [] means no conditions
    route: str | None = None                # the next step after the follow-up tools, or "answer"
    tools: list[str] | None = None          # the tools that the model called, in order
    pick_numbers: list[int] | None = None   # the picks that were removed
    searched: bool | None = None
    says: str | None = None                 # a text in the replies of the turn
    not_says: str | None = None             # a text that no reply of the turn may have
    picks: list[str] | None = None          # a text in the title of each pick, in order. Later turns refer to them


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    expect: Expect = Field(default_factory=Expect)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    note: str = ""
    turns: list[Turn]


def load_scripts(path: Path) -> list[Script]:
    return [Script.model_validate(s) for s in yaml.safe_load(path.read_text(encoding="utf-8"))]


def stored_record(card: Card) -> ProductRecord:
    """The stored facts of a card. The evaluation never opens amazon.se."""
    return ProductRecord(source="amazon_se", source_id=card.url.rstrip("/").split("/")[-1], url=card.url,
                         fetched_at=card.fetched_at, title=card.title, brand=card.brand, model=card.model,
                         price=card.price, currency=card.currency, in_stock=card.in_stock,
                         attributes=card.attributes)


def offline_deps(conn, llm, k: int = 5) -> tuple[Deps, list]:
    """The same deps as scout chat, over the stored products. A search only records its words and page. There is no
    size check, because the records do not hold the page text. Rejections stay in memory."""
    removed: dict[str, set[int]] = {}
    searches: list[tuple[str, int]] = []

    def crawl(profile, needs, page):
        searches.append((needs.search_terms(profile), page))
        return CrawlReport(notes=["Evaluation run: no search on amazon.se, only the stored products."])

    deps = shared_deps(
        conn, llm, crawl, lambda profile: stored_record, k, size_check=False,
        eliminate=lambda thread, ids, reason: removed.setdefault(thread, set()).update(ids),
        eliminated=lambda thread: set(removed.get(thread, set())),
        restore=lambda thread, ids: removed.setdefault(thread, set()).difference_update(ids),
        say=lambda text: None,
    )
    return deps, searches


def compare(expect: Expect, state: dict, route: dict | None, searched: bool, replies: list[str]) -> list[dict]:
    """One entry for each expected value: its name, the expected and the actual value, and if they agree."""
    needs = Needs.model_validate(state.get("needs") or {})
    actual = {
        "phase": state.get("phase"),
        "category": needs.category or "none",
        "kind": needs.kind,
        "budget": needs.budget,
        "no_budget": needs.budget is None,
        "conditions": sorted(f"{c.attribute}{c.op}{c.value:g}" for c in needs.conditions),
        "route": (route or {}).get("action"),
        "tools": (route or {}).get("tools"),
        "pick_numbers": (route or {}).get("pick_numbers"),
        "searched": searched,
    }
    titles = [p.get("title") or "" for p in state.get("picks") or []]
    checks = []
    for name, wanted in expect.model_dump(exclude_none=True, exclude_defaults=True).items():
        if name in ("says", "not_says"):
            got = any(wanted in r for r in replies)
            ok = got if name == "says" else not got
            checks.append({"check": name, "expected": wanted, "actual": got, "ok": ok})
            continue
        if name == "picks":
            ok = len(titles) == len(wanted) and all(w in t for w, t in zip(wanted, titles))
            checks.append({"check": name, "expected": wanted, "actual": [t[:40] for t in titles], "ok": ok})
            continue
        if name == "conditions":
            wanted = sorted(wanted)
        checks.append({"check": name, "expected": wanted, "actual": actual[name], "ok": actual[name] == wanted})
    return checks


def step_results(calls) -> dict:
    """The rounds of each checked step in a turn, and if its last round passed. A round is a reply that matched the
    schema. A step that ended with a reply that did not match the schema did not pass."""
    steps = {}
    for schema, step in CHECKED.items():
        mine = [c for c in calls if c.schema == schema]
        rounds = sum(1 for c in mine if c.valid)
        if rounds:
            passed = mine[-1].valid and mine[-1].checked is True
            steps[step] = {"rounds": rounds, "passed": passed, "first_round": passed and rounds == 1}
    return steps


def cards_text(deps: Deps, picks: list[dict]) -> list[str]:
    """The numbered cards as the chat model sees them, for the grader."""
    return [card_text(n, c) for n, c in enumerate(deps.cards([p["listing_id"] for p in picks]), 1)] if picks else []


def run_scripts(scripts: list[Script], deps: Deps, searches: list, llm, progress=lambda text: None,
                after_turn=lambda: None) -> list[dict]:
    """Run each script in a new thread. Return one record for each turn. `after_turn` runs after each turn, for
    example to end the database transaction."""
    records = []
    for script in scripts:
        graph = build_graph(deps, InMemorySaver())
        config = {"configurable": {"thread_id": f"eval-{script.id}"}}
        picks_differ = False
        for number, turn in enumerate(script.turns, 1):
            calls_before, searches_before, started = len(llm.calls), len(searches), time.monotonic()
            values = graph.get_state(config).values
            before, shown = len(values.get("messages", [])), values.get("picks", [])
            route, error = None, None
            try:
                for update in graph.stream({"messages": [{"role": "user", "content": turn.say}]}, config,
                                           stream_mode="updates"):
                    if (update.get("act") or {}).get("route"):
                        route = update["act"]["route"]
            except Exception as exc:  # report it, and go on with the next turn
                error = f"{type(exc).__name__}: {exc}"
            after_turn()
            state = graph.get_state(config).values
            replies = [m["content"] for m in state.get("messages", [])[before + 1:]]
            error = error or next((r for r in replies if r.startswith(NODE_ERRORS)), None)
            calls = llm.calls[calls_before:]
            after = state.get("picks", [])
            checks = compare(turn.expect, state, route, len(searches) > searches_before, replies)
            not_measured = PICKS_DIFFER if picks_differ else error
            picks_differ = picks_differ or any(c["check"] == "picks" and not c["ok"] for c in checks)
            records.append({
                "script": script.id, "turn": number, "say": turn.say, "replies": replies, "error": error,
                "measured": not_measured is None, "not_measured": not_measured,
                "checks": checks, "steps": step_results(calls), "seconds": round(time.monotonic() - started, 1),
                "cards": cards_text(deps, shown), "actions": route, "searched": len(searches) > searches_before,
                "new_cards": cards_text(deps, after) if after != shown else [],
                "rounds": [{"schema": c.schema, "passed": c.checked, "problems": c.problems, "output": c.output}
                           for c in calls if c.checked is not None],
                "calls": [{"role": c.role, "model": c.model, "schema": c.schema, "valid": c.valid,
                           "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "cost": c.cost}
                          for c in calls],
            })
            failed = sum(not c["ok"] for c in records[-1]["checks"])
            progress(f"{script.id} turn {number}: {records[-1]['seconds']} s, {len(calls)} LLM calls, "
                     f"{failed} values do not agree{', error' if error else ''}"
                     f"{', not measured' if not_measured else ''}")
    return records


def summarize(records: list[dict]) -> dict:
    """The expected values and the checked steps count only in measured turns. Calls, tokens and time count in all."""
    measured = [r for r in records if r["measured"]]
    checks = Counter()
    for record in measured:
        for check in record["checks"]:
            checks[(check["check"], check["ok"])] += 1
    names = sorted({name for name, _ in checks})
    steps = {}
    for record in measured:
        for step, result in record["steps"].items():
            entry = steps.setdefault(step, {"ran": 0, "first_round": 0, "passed": 0})
            entry["ran"] += 1
            entry["first_round"] += result["first_round"]
            entry["passed"] += result["passed"]
    models = {}
    for record in records:
        for call in record["calls"]:
            entry = models.setdefault(call["model"], {"calls": 0, "not_valid": 0, "input_tokens": 0, "output_tokens": 0})
            entry["calls"] += 1
            entry["not_valid"] += not call["valid"]
            entry["input_tokens"] += call["input_tokens"]
            entry["output_tokens"] += call["output_tokens"]
            if call.get("cost") is not None:
                entry["cost_usd"] = round(entry.get("cost_usd", 0) + call["cost"], 6)
    seconds = [r["seconds"] for r in records]
    return {
        "turns": len(records),
        "errors": sum(1 for r in records if r["error"]),
        "not_measured": len(records) - len(measured),
        "checks": {name: {"ok": checks[(name, True)], "total": checks[(name, True)] + checks[(name, False)]}
                   for name in names},
        "steps": steps,
        "models": models,
        "seconds": {"median": statistics.median(seconds) if seconds else 0, "max": max(seconds, default=0)},
    }


def summary_text(summary: dict) -> str:
    lines = [f"turns {summary['turns']}, turns with an error {summary['errors']}, not measured "
             f"{summary['not_measured']} (an outage, a program error, or picks that differ from the script), "
             f"seconds per turn: median {summary['seconds']['median']}, max {summary['seconds']['max']}",
             "Expected values (measured turns only):"]
    lines += [f"  {name}: {c['ok']} of {c['total']}" for name, c in summary["checks"].items()]
    lines.append("Checked steps (a round is one reply of the model that matched the schema):")
    lines += [f"  {step}: ran {s['ran']}, passed in round 1: {s['first_round']}, passed in the end: {s['passed']}"
              for step, s in summary["steps"].items()]
    lines.append("LLM calls:")
    lines += [f"  {model}: {m['calls']} calls ({m['not_valid']} not valid JSON), tokens in {m['input_tokens']}, "
              f"out {m['output_tokens']}" + (f", cost {m['cost_usd']:.4f} USD" if "cost_usd" in m else "")
              for model, m in summary["models"].items()]
    return "\n".join(lines)
