"""Replay all eval-search runs from their snapshots (no page loads, no model). Writes eval/results/replay.json."""
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from scout.db.store import load_filters  # noqa: E402
from scout.eval.hidden import load_targets  # noqa: E402
from scout.eval.replay import Run, list_places, methods, run_lists, score_methods  # noqa: E402
from scout.profiles import load_profile  # noqa: E402
from scout.search.filters import word_filters  # noqa: E402


def D(text):
    return datetime.strptime(text, "%Y%m%dT%H%M")


RUNS = [  # UTC windows of the runs, from the fetch log; the name is the local end time of the result file
    (Run("1016-0205", D("20261015T2340"), D("20261016T0006")), "search-20261016-0205.json", "targets.yaml"),
    (Run("1016-0226", D("20261016T0006"), D("20261016T0028")), "search-20261016-0226.json", "targets.yaml"),
    (Run("1017-1824", D("20261017T1600"), D("20261017T1625")), "search-20261017-1824.json", "targets.yaml"),
    (Run("1017-1853", D("20261017T1625"), D("20261017T1655")), "search-20261017-1853.json", "targets.yaml"),
    (Run("1017-2037", D("20261017T1800"), D("20261017T1840")), "search-20261017-2037.json", "targets.yaml"),
    (Run("1018-1741", D("20261018T1525"), D("20261018T1545")), "search-20261018-1741.json", "heldout.yaml"),
]

def main(only=None, rerank=None, out_name="replay.json"):
    conn = psycopg.connect("postgresql://scout:scout@localhost:5433/product_scout_eval", row_factory=dict_row)
    out = []
    for run, result_file, target_file in RUNS:
        if only and run.name not in only:
            continue
        targets = {t.id: t for t in load_targets(ROOT / "eval" / "hidden" / target_file)}
        results = json.loads((ROOT / "eval" / "results" / result_file).read_text(encoding="utf-8"))
        for r in results:
            if r.get("valid") is False or r["target"] not in targets:
                continue
            t = targets[r["target"]]
            profile = load_profile(t.category)
            chosen = r["with filters"].get("filters") or []
            words, _ = word_filters(t.search.filters, load_filters(conn, "amazon_se", t.category))
            words = [w for w in words if w in chosen]
            safe = [f for f in chosen if f not in words]
            lists = run_lists(t, {"none": [], "safe": safe, "all": chosen if words else []}, ROOT / "snapshots", run)
            out.append({"run": run.name, "target": t.id, "category": t.category, "set": target_file,
                        "lists": list_places(t, lists), "sizes": {f"{s or 'default'}/{k}": sum(map(len, p))
                                                                 for (s, k), p in lists.items()},
                        "methods": score_methods(t, profile, methods(lists), rerank),
                        "eval": {k: r[k]["place"] for k in ("before", "without filters", "with filters") if k in r}})
            print(run.name, t.id, {k: v["place"] for k, v in out[-1]["methods"].items()}, flush=True)
    (ROOT / "eval" / "results" / out_name).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
