"""Entry points for Rote Plays: small commands that print one JSON document.

    .venv/bin/python -m causal_crew.plays health  --orders data/orders.parquet
    .venv/bin/python -m causal_crew.plays learn   --orders data/orders.parquet
    .venv/bin/python -m causal_crew.plays replay  --recipe memory/recipes/west.json --orders data/orders.parquet

health  Is the number real? The deterministic health check, no LLM.
learn   The full crew (planner and investigators steered by the LLM). Every SUPPORTED
        finding's drill-down path is saved as a recipe.
replay  Muscle memory: re-run a recipe's drill-down path on fresh data with no LLM
        calls, then judge it with the same deterministic evidence checks.

Windows default to the latest N days of data against the N days before them.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import duckdb

from causal_crew import config as C
from causal_crew import health, investigator, judge, memory
from causal_crew import run as pipeline

RECIPE_DIR = os.path.join(os.path.dirname(memory.MEMORY_PATH), "recipes")


def latest_windows(orders_path, days=C.QUESTION_DEFAULT_DAYS):
    with duckdb.connect() as con:
        end = con.execute("SELECT max(date) FROM read_parquet(?)", [orders_path]).fetchone()[0]
    end = end.date() if hasattr(end, "date") else end
    c0 = end - timedelta(days=days - 1)
    return {"baseline": (c0 - timedelta(days=days), c0 - timedelta(days=1)), "current": (c0, end)}


def _windows(args):
    if args.current:
        c0, c1 = (date.fromisoformat(d) for d in args.current)
        if args.baseline:
            b0, b1 = (date.fromisoformat(d) for d in args.baseline)
        else:
            n = (c1 - c0).days + 1
            b0, b1 = c0 - timedelta(days=n), c0 - timedelta(days=1)
        return {"baseline": (b0, b1), "current": (c0, c1)}
    return latest_windows(args.orders, args.days)


def _iso(windows):
    return {k: [d.isoformat() for d in v] for k, v in windows.items()}


def health_play(orders_path, windows):
    result = health.run(orders_path, windows=windows, expected_latest=windows["current"][1])
    return {"play": "health", "data": os.path.basename(orders_path), "windows": _iso(windows),
            "trust": "OK" if result["status"] == "PASS" else "DON'T TRUST THIS NUMBER YET", **result}


def recipe_from(finding, question, windows):
    """What a verified investigation learned: where it started and which way it went."""
    return {"lead_id": finding["lead_id"], "hypothesis": finding.get("hypothesis", ""),
            "learned_from": {"question": question, "windows": windows,
                             "verdict": finding["verdict"], "contribution": finding["contribution"],
                             "change_point": finding["change_point"]},
            "start_segment": finding["start_segment"],
            "path": [lvl["chosen"] for lvl in finding["drill_path"] if lvl["chosen"]]}


def learn_play(orders_path, windows, question, recipe_dir=RECIPE_DIR, **run_kwargs):
    t0 = time.time()
    report = pipeline.run(orders_path, question=question, windows=_iso(windows),
                          **run_kwargs)
    out = {"play": "learn", "data": os.path.basename(orders_path), "windows": report["windows"],
           "health": report["health"]["status"], "outcome": report["outcome"], "recipes": [],
           "llm_engines": [], "seconds": round(time.time() - t0, 1), "report": report["files"]["markdown"]}
    if report["outcome"] == "STOPPED":
        out["failed"] = report["health"]["failed"]
        return out
    out["llm_engines"] = [report["plan"]["planner"]] + [
        lvl["decided_by"] for inv in report["investigations"] for lvl in inv["drill_path"]]
    os.makedirs(recipe_dir, exist_ok=True)
    for f in report["judgement"]["findings"]:
        out.setdefault("findings", []).append({k: f[k] for k in ("lead_id", "segment", "contribution", "verdict")})
        if f["verdict"].startswith("SUPPORTED"):
            path = os.path.join(recipe_dir, f"{f['lead_id']}.json")
            with open(path, "w") as fh:
                json.dump(recipe_from(f, report["question"], report["windows"]), fh, indent=2)
            out["recipes"].append(path)
    return out


def recipe_decider(path):
    """Follow the saved path. Each step records whether today's data still supports it."""
    def decide(lead_id, hypothesis, segment, level, candidates):
        if level > len(path):
            return {"dimension": None, "value": None, "decided_by": "recipe", "reason": "end of the saved path"}
        step = path[level - 1]
        c = next((c for c in candidates if c["dimension"] == step["dimension"]), None)
        still = bool(c and c["top_value"] == str(step["value"])
                     and c["concentration"] >= C.DRILL_MIN_CONCENTRATION)
        return {"dimension": step["dimension"], "value": step["value"], "decided_by": "recipe",
                "reason": "saved path; " + ("today's data still supports this step" if still
                                            else "today's data no longer singles this step out"),
                "still_supported": still}
    return decide


def replay_play(recipe, orders_path, windows, root=C.WORKSPACE_DIR, events=None):
    t0 = time.time()
    checked = health_play(orders_path, windows)
    out = {"play": "replay", "lead_id": recipe["lead_id"], "data": os.path.basename(orders_path),
           "windows": _iso(windows), "health": checked["status"], "llm_calls": 0}
    if checked["status"] == "FAIL":
        out.update(outcome="STOPPED", failed=checked["failed"], seconds=round(time.time() - t0, 1))
        return out
    decide = recipe_decider(recipe["path"])
    steps = []

    def tracking(*a):
        d = decide(*a)
        steps.append({k: d[k] for k in ("dimension", "value", "still_supported") if k in d})
        return d

    finding = investigator.investigate(recipe["lead_id"], recipe["start_segment"], recipe.get("hypothesis", ""),
                                       windows=windows, orders_path=orders_path, events=events,
                                       root=root, decide=tracking)
    judged = judge.judge([finding], orders_path=orders_path, windows=windows)["findings"][0]
    out.update(outcome="REPLAYED", segment=judged["segment"], contribution=judged["contribution"],
               change_point=judged["change_point"], volume_rate=judged["volume_rate"],
               linked_context=judged["linked_context"], verdict=judged["verdict"],
               checks={k: v["ok"] for k, v in judged["checks"].items()},
               path=[s for s in steps if s.get("dimension")],
               path_still_supported=all(s.get("still_supported", True) for s in steps if s.get("dimension")),
               queries=len(finding["queries"]),
               seconds=round(time.time() - t0, 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="play", required=True)
    for name in ("health", "learn", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--orders", default=C.ORDERS_PATH, help="orders parquet file")
        p.add_argument("--current", nargs=2, metavar=("START", "END"), help="current window (ISO dates)")
        p.add_argument("--baseline", nargs=2, metavar=("START", "END"), help="baseline window (ISO dates)")
        p.add_argument("--days", type=int, default=C.QUESTION_DEFAULT_DAYS,
                       help="window length when no dates are given")
        if name == "learn":
            p.add_argument("-q", "--question", default=C.DEMO_QUESTION)
        if name == "replay":
            p.add_argument("--recipe", required=True, help="recipe JSON written by learn")
    args = ap.parse_args(argv)
    windows = _windows(args)
    if args.play == "health":
        out = health_play(args.orders, windows)
    elif args.play == "learn":
        out = learn_play(args.orders, windows, args.question)
    else:
        with open(args.recipe) as f:
            out = replay_play(json.load(f), args.orders, windows)
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 1 if out.get("outcome") == "STOPPED" or out.get("status") == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
