"""Run the whole Causal Crew pipeline and write a report.

    .venv/bin/python -m causal_crew.run                         # demo run B: clean data
    .venv/bin/python -m causal_crew.run --broken                # demo run A: a day loaded twice
    .venv/bin/python -m causal_crew.run -q "Why did revenue move over the past month?"

Stages: question -> health check -> planner -> investigators in parallel ->
judge -> memory -> report. If the health check fails, the run stops and says why.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import duckdb

from causal_crew import config as C
from causal_crew import health, investigator, memory, planner
from causal_crew import judge as judge_mod
from causal_crew import question as questions

REPORT_DIR = os.path.join(os.path.dirname(C.WORKSPACE_DIR), "reports")
HISTORY_DIR = os.path.join(REPORT_DIR, "history")


def _headline(orders_path, windows):
    """Revenue in the baseline and current windows: the number being explained."""
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    with duckdb.connect() as con:
        base, cur = con.execute(
            "SELECT sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), "
            "sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END) FROM read_parquet(?)",
            [b0, b1, c0, c1, orders_path]).fetchone()
    return {"baseline": round(base, 2), "current": round(cur, 2), "change": round(cur / base - 1, 4)}


def run(orders_path=C.ORDERS_PATH, question=C.DEMO_QUESTION, windows=None, out_dir=REPORT_DIR, ask=None,
        root=C.WORKSPACE_DIR, memory_path=memory.MEMORY_PATH, remember=False, decide=None, on_event=None):
    """Answer one question end to end.

    windows: optional {"baseline": [start, end], "current": [start, end]}; without it the
    question is read by the LLM (validated) or the rule. on_event(stage, status, detail)
    reports progress to the web app.
    """
    emit = on_event or (lambda *_args: None)
    t0 = time.time()
    emit("question", "running", {})
    interp = questions.interpret(question, orders_path, ask=ask, windows=windows)
    w = interp["windows"]
    report = {"id": f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "question": question, "data": os.path.basename(orders_path),
              "windows": {k: [d.isoformat() for d in v] for k, v in w.items()},
              "interpretation": {k: interp[k] for k in ("source", "note", "data_range")}}
    emit("question", "done", {"windows": report["windows"], "source": interp["source"]})

    emit("health", "running", {})
    report["health"] = health.run(orders_path, windows=w, expected_latest=w["current"][1])
    emit("health", report["health"]["status"].lower(), {"failed": report["health"]["failed"]})
    if report["health"]["status"] == "FAIL":
        report["outcome"] = "STOPPED"
        result = _finish(report, out_dir, t0)
        emit("run", "stopped", {})
        return result

    report["headline"] = _headline(orders_path, w)
    emit("planner", "running", {})
    report["plan"] = planner.plan(question=question, orders_path=orders_path, windows=w, ask=ask,
                                  memory_records=memory.recall(memory_path))
    leads = report["plan"]["leads"]
    emit("planner", "done", {"engine": report["plan"]["planner"],
                              "leads": [{k: lead[k] for k in ("lead_id", "segment", "source")}
                                        for lead in leads]})
    decide = decide or investigator.llm_decider

    def investigate(lead):
        emit("investigator", "running", {"lead_id": lead["lead_id"]})
        finding = investigator.investigate(lead["lead_id"], lead["segment"], lead["hypothesis"], windows=w,
                                           orders_path=orders_path, root=root, decide=decide)
        emit("investigator", "done", {"lead_id": lead["lead_id"],
                                       "decided_by": [lvl["decided_by"] for lvl in finding["drill_path"]]})
        return finding

    with ThreadPoolExecutor(max_workers=len(leads)) as pool:
        findings = list(pool.map(investigate, leads))
    report["investigations"] = findings
    emit("judge", "running", {})
    report["judgement"] = judge_mod.judge(findings, orders_path=orders_path, windows=w)
    emit("judge", "done", {"verdicts": {f["lead_id"]: f["verdict"] for f in report["judgement"]["findings"]}})
    recorded = memory.write_back(report["judgement"]["findings"], report["question"],
                                 report["windows"], memory_path)
    report["memory"] = {"prior": report["plan"]["memory_considered"], "recorded": recorded, "cognee": None}
    if remember and recorded:
        try:
            n = asyncio.run(memory.remember_in_cognee(recorded))
            report["memory"]["cognee"] = f"stored {n} finding(s)"
        except Exception as e:  # Cognee is an optional extra, never a reason to fail the run
            report["memory"]["cognee"] = f"skipped: {type(e).__name__}: {str(e)[:150]}"
    emit("memory", "done", {"recorded": len(recorded)})
    report["outcome"] = "INVESTIGATED"
    result = _finish(report, out_dir, t0)
    emit("run", "done", {})
    return result


def _finish(report, out_dir, t0):
    report["seconds"] = round(time.time() - t0, 1)
    os.makedirs(out_dir, exist_ok=True)
    stem = "run_a_broken" if report["outcome"] == "STOPPED" else "run_b_clean"
    report["files"] = {"json": os.path.join(out_dir, f"{stem}.json"),
                       "markdown": os.path.join(out_dir, f"{stem}.md")}
    with open(report["files"]["json"], "w") as f:
        json.dump(report, f, indent=2, default=str)
    history = os.path.join(out_dir, "history")
    os.makedirs(history, exist_ok=True)
    with open(os.path.join(history, f"{report['id']}.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    with open(report["files"]["markdown"], "w") as f:
        f.write(render_markdown(report))
    return report


def _cell(x):
    """Escape pipes so evidence like |z| doesn't split a Markdown table cell."""
    return str(x).replace("|", "\\|")


def _pct(x):
    return "—" if x is None else f"{x:.1%}"


def render_markdown(r):
    out = [f"# Causal Crew report — {r['data']}", "",
           "_Synthetic demo data with a planted cause. Causal Crew reports which segments "
           "account for a change and what context lines up with it; it does not prove causation._", "",
           f"**Question:** {r['question']}  ",
           f"**Windows:** baseline {r['windows']['baseline'][0]} → {r['windows']['baseline'][1]}, "
           f"current {r['windows']['current'][0]} → {r['windows']['current'][1]}", "",
           f"## 1. Can this number be trusted? {r['health']['status']}", "",
           "| check | result | evidence |", "|---|---|---|"]
    for c in r["health"]["checks"]:
        out.append(f"| {c['name']} | {'PASS' if c['ok'] else '**FAIL**'} | {_cell(c['evidence'])} |")
    if r["outcome"] == "STOPPED":
        out += ["", f"**Don't trust this number yet.** Failed: {', '.join(r['health']['failed'])}. "
                    "The investigation stopped here; fix the data before explaining the change.", ""]
        return "\n".join(out)

    h = r.get("headline")
    if h:
        out += ["", f"**Revenue:** ${h['baseline']:,.0f} → ${h['current']:,.0f} ({h['change']:+.1%})"]
    p = r["plan"]
    engine = {"rocketride": "Gemini via RocketRide", "gemini": "Gemini direct",
              "fallback": "keyword fallback, LLM unavailable"}.get(p["planner"], p["planner"])
    out += ["", f"## 2. Leads ({engine})", ""]
    for lead in p["leads"]:
        src = f"context: `{lead['event_file']}`" if lead["event_file"] else lead["source"]
        out.append(f"- **{lead['lead_id']}** `{lead['segment']}` ({src}) — {lead['hypothesis']}")

    out += ["", "## 3. Findings, ranked by the judge", "",
            "| # | lead | segment | share of change | verdict |", "|---|---|---|---|---|"]
    for f in r["judgement"]["findings"]:
        out.append(f"| {f['rank']} | {f['lead_id']} | `{_cell(f['segment'])}` | "
                   f"{_pct(f['contribution'])} | {_cell(f['verdict'])} |")

    for f in r["judgement"]["findings"]:
        out += ["", f"### #{f['rank']} {f['lead_id']} — {f['verdict']}", ""]
        if f["merged_into"]:
            out.append(f"Explains largely the same orders as **{f['merged_into']}** "
                       f"({f['overlap']:.0%} of its lost orders overlap), so it was merged rather than double-counted.")
            continue
        vr = f["volume_rate"] or {}
        out += [f"- **Accounts for** {_pct(f['contribution'])} of the total change "
                f"(starting lead: {_pct(f['lead_contribution'])})",
                "- **Drill-down:** " + " → ".join(
                    [str(f["start_segment"])] + [f"{lvl['chosen']['dimension']}={lvl['chosen']['value']}"
                                                  for lvl in f["drill_path"] if lvl["chosen"]]),
                *[f"  - level {lvl['level']}: {lvl['decided_by']} — {lvl['reason']}"
                  for lvl in f["drill_path"]],
                f"- **Change point:** {f['change_point']}",
                f"- **Volume vs rate:** orders {vr.get('orders_base', '—'):,} → {vr.get('orders_current', '—'):,}, "
                f"average order ${vr.get('aov_base', 0):,.2f} → ${vr.get('aov_current', 0):,.2f}; "
                f"volume explains {_pct(vr.get('volume_share'))} of the change"]
        ctx = f["linked_context"]
        if ctx:
            out.append(f"- **Lines up with:** {ctx['title']} ({ctx['date']}, "
                       f"{ctx['days_from_change_point']:+d} days from the change point)")
        out.append("- **Evidence checks:**")
        for name, c in f["checks"].items():
            out.append(f"  - {name}: {'PASS' if c['ok'] else 'FAIL'} — {c['evidence']}")

    m = r["memory"]
    out += ["", "## 4. Memory", "", f"- Prior verified findings the planner saw: {len(m['prior'])}"]
    out += [f"  - {p}" for p in m["prior"]]
    out.append(f"- Newly recorded for the next question: {len(m['recorded'])}")
    out += [f"  - {memory.describe(rec)}" for rec in m["recorded"]]
    if m["cognee"]:
        out.append(f"- Cognee: {m['cognee']}")

    out += ["", "## Appendix: the SQL behind every number", ""]
    for inv in r["investigations"]:
        out += [f"<details><summary>{inv['lead_id']} ({len(inv['queries'])} queries, "
                f"workspace tables: {', '.join(inv['tables'])})</summary>", "", "```sql"]
        out += [q + ";" for q in inv["queries"]]
        out += ["```", "</details>", ""]
    out += [f"<details><summary>judge ({len(r['judgement']['queries'])} queries)</summary>", "", "```sql"]
    out += [q + ";" for q in r["judgement"]["queries"]]
    out += ["```", "</details>", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broken", action="store_true", help="run on the broken copy (demo run A)")
    ap.add_argument("--remember", action="store_true", help="also store verified findings in Cognee (slow)")
    ap.add_argument("-q", "--question", default=C.DEMO_QUESTION, help="the question to answer")
    args = ap.parse_args()
    r = run(C.ORDERS_BROKEN_PATH if args.broken else C.ORDERS_PATH, question=args.question, remember=args.remember)
    print(f"question: {r['question']}")
    print(f"windows: baseline {r['windows']['baseline'][0]}..{r['windows']['baseline'][1]}, "
          f"current {r['windows']['current'][0]}..{r['windows']['current'][1]} "
          f"(dates from {r['interpretation']['source']})")
    print(f"health: {r['health']['status']}  outcome: {r['outcome']}  ({r['seconds']}s)")
    if r["outcome"] == "STOPPED":
        for c in r["health"]["checks"]:
            if not c["ok"]:
                print(f"  FAIL {c['name']}: {c['evidence']}")
    else:
        print(f"planner: {r['plan']['planner']}")
        engines = [lvl["decided_by"] for inv in r["investigations"] for lvl in inv["drill_path"]]
        print("investigator decisions: " + ", ".join(f"{e} x{engines.count(e)}" for e in sorted(set(engines))))
        for f in r["judgement"]["findings"]:
            print(f"  #{f['rank']} {f['lead_id']:24s} {_pct(f['contribution']):>6s}  {f['verdict']}")
        m = r["memory"]
        print(f"memory: {len(m['prior'])} prior finding(s) considered, {len(m['recorded'])} recorded"
              + (f", cognee {m['cognee']}" if m["cognee"] else ""))
    print(f"report: {r['files']['markdown']}")


if __name__ == "__main__":
    main()
