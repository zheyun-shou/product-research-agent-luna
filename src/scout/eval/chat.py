"""Scripted conversations: the real chat model talks with the graph over stored products, and Python scores it."""
import statistics
import time
from collections import Counter
from pathlib import Path

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from scout.agent.graph import Deps, build_graph
from scout.crawl import CrawlReport
from scout.needs import Needs
from scout.picks.pipeline import make_picks, shortlist
from scout.picks.rank import Card, load_cards_by_ids
from scout.profiles import PROFILES_DIR, load_profile
from scout.sources.base import ProductRecord

# The steps whose output Python checks, by schema name, and the reply text when the last round still failed.
CHECKED = {"ClarifyReply": ("clarify", "The needs were still not valid"),
           "PicksText": ("summaries", "did not pass the checks"),
           "Act": ("actions", "so I changed nothing"),
           "CalcPlan": ("calculations", "I could not turn this request")}


NODE_ERRORS = ("I cannot finish this step now", "Something went wrong in the step")


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
    """Deps over the stored products. A search only records its words and page. Rejections stay in memory."""
    removed: dict[str, set[int]] = {}
    screened: dict[tuple[str, int], bool] = {}
    searches: list[tuple[str, int]] = []

    def crawl(profile, needs, page):
        searches.append((needs.search_terms(profile), page))
        return CrawlReport(notes=["Evaluation run: no search on amazon.se, only the stored products."])

    def picks(profile, needs, excluded, kept, first):
        short = shortlist(conn, profile, needs, llm, set(excluded) | {c.product_id for c in [*kept, *first]}, screened)
        return make_picks(short, needs, k, stored_record, llm, kept=kept, first=first)

    deps = Deps(
        llm=llm,
        profile_names=lambda: sorted(p.stem for p in PROFILES_DIR.glob("*.yaml")),
        load_profile=load_profile,
        count=lambda profile, needs, excluded: len(shortlist(conn, profile, needs, llm, excluded, screened).ranked),
        crawl=crawl,
        picks=picks,
        cards=lambda ids: load_cards_by_ids(conn, ids),
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
    checks = []
    for name, wanted in expect.model_dump(exclude_none=True, exclude_defaults=True).items():
        if name in ("says", "not_says"):
            got = any(wanted in r for r in replies)
            ok = got if name == "says" else not got
            checks.append({"check": name, "expected": wanted, "actual": got, "ok": ok})
            continue
        if name == "conditions":
            wanted = sorted(wanted)
        checks.append({"check": name, "expected": wanted, "actual": actual[name], "ok": actual[name] == wanted})
    return checks


def run_scripts(scripts: list[Script], deps: Deps, searches: list, llm, progress=lambda text: None) -> list[dict]:
    """Run each script in a new thread. Return one record for each turn."""
    records = []
    for script in scripts:
        graph = build_graph(deps, InMemorySaver())
        config = {"configurable": {"thread_id": f"eval-{script.id}"}}
        for number, turn in enumerate(script.turns, 1):
            calls_before, searches_before, started = len(llm.calls), len(searches), time.monotonic()
            before = len(graph.get_state(config).values.get("messages", []))
            route, error = None, None
            try:
                for update in graph.stream({"messages": [{"role": "user", "content": turn.say}]}, config,
                                           stream_mode="updates"):
                    if (update.get("act") or {}).get("route"):
                        route = update["act"]["route"]
            except Exception as exc:  # report it, and go on with the next turn
                error = f"{type(exc).__name__}: {exc}"
            state = graph.get_state(config).values
            replies = [m["content"] for m in state.get("messages", [])[before + 1:]]
            # A node caught the problem (quota, server, browser), so the model was not measured in this turn.
            error = error or next((r for r in replies if r.startswith(NODE_ERRORS)), None)
            calls = llm.calls[calls_before:]
            steps = {}
            for schema, (step, failed_text) in CHECKED.items():
                rounds = sum(1 for c in calls if c.schema == schema and c.valid)
                if rounds:
                    passed = not any(failed_text in r for r in replies)
                    steps[step] = {"rounds": rounds, "passed": passed, "first_round": passed and rounds == 1}
            records.append({
                "script": script.id, "turn": number, "say": turn.say, "replies": replies, "error": error,
                "checks": compare(turn.expect, state, route, len(searches) > searches_before, replies),
                "steps": steps, "seconds": round(time.monotonic() - started, 1),
                "calls": [{"role": c.role, "model": c.model, "schema": c.schema, "valid": c.valid,
                           "input_tokens": c.input_tokens, "output_tokens": c.output_tokens} for c in calls],
            })
            failed = sum(not c["ok"] for c in records[-1]["checks"])
            progress(f"{script.id} turn {number}: {records[-1]['seconds']} s, {len(calls)} LLM calls, "
                     f"{failed} values do not agree{', error' if error else ''}")
    return records


def summarize(records: list[dict]) -> dict:
    checks = Counter()
    for record in records:
        for check in record["checks"]:
            checks[(check["check"], check["ok"])] += 1
    names = sorted({name for name, _ in checks})
    steps = {}
    for record in records:
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
    seconds = [r["seconds"] for r in records]
    return {
        "turns": len(records),
        "errors": sum(1 for r in records if r["error"]),
        "checks": {name: {"ok": checks[(name, True)], "total": checks[(name, True)] + checks[(name, False)]}
                   for name in names},
        "steps": steps,
        "models": models,
        "seconds": {"median": statistics.median(seconds) if seconds else 0, "max": max(seconds, default=0)},
    }


def summary_text(summary: dict) -> str:
    lines = [f"turns {summary['turns']}, turns with an error {summary['errors']}, "
             f"seconds per turn: median {summary['seconds']['median']}, max {summary['seconds']['max']}",
             "Expected values:"]
    lines += [f"  {name}: {c['ok']} of {c['total']}" for name, c in summary["checks"].items()]
    lines.append("Checked steps (a round is one reply of the model that matched the schema):")
    lines += [f"  {step}: ran {s['ran']}, passed in round 1: {s['first_round']}, passed in the end: {s['passed']}"
              for step, s in summary["steps"].items()]
    lines.append("LLM calls:")
    lines += [f"  {model}: {m['calls']} calls ({m['not_valid']} not valid JSON), tokens in {m['input_tokens']}, "
              f"out {m['output_tokens']}" for model, m in summary["models"].items()]
    return "\n".join(lines)
