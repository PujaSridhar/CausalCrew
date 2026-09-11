"""Run the whole Causal Crew pipeline locally and write a report.

    .venv/bin/python -m causal_crew.run            # demo run B: clean data
    .venv/bin/python -m causal_crew.run --broken   # demo run A: a day loaded twice

Stages: health check -> planner -> investigators in parallel -> judge ->
report. If the health check fails, the run stops and says why.
"""

import argparse
import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from causal_crew import config as C
from causal_crew import health, investigator, memory, planner
from causal_crew import judge as judge_mod

REPORT_DIR = os.path.join(os.path.dirname(C.WORKSPACE_DIR), "reports")


def run(orders_path=C.ORDERS_PATH, out_dir=REPORT_DIR, ask=None, root=C.WORKSPACE_DIR,
        memory_path=memory.MEMORY_PATH, remember=False):
    t0 = time.time()
    report = {"question": C.DEMO_QUESTION, "data": os.path.basename(orders_path),
              "windows": {"baseline": list(C.DEMO_BASELINE_WINDOW), "current": list(C.DEMO_CURRENT_WINDOW)}}

    report["health"] = health.run(orders_path)
    if report["health"]["status"] == "FAIL":
        report["outcome"] = "STOPPED"
        return _finish(report, out_dir, t0)

    report["plan"] = planner.plan(orders_path=orders_path, ask=ask,
                                  memory_records=memory.recall(memory_path))
    leads = report["plan"]["leads"]
    with ThreadPoolExecutor(max_workers=len(leads)) as pool:
        findings = list(pool.map(
            lambda lead: investigator.investigate(lead["lead_id"], lead["segment"], lead["hypothesis"],
                                                  orders_path=orders_path, root=root), leads))
    report["investigations"] = findings
    report["judgement"] = judge_mod.judge(findings, orders_path=orders_path)
    recorded = memory.write_back(report["judgement"]["findings"], report["question"],
                                 report["windows"], memory_path)
    report["memory"] = {"prior": report["plan"]["memory_considered"], "recorded": recorded, "cognee": None}
    if remember and recorded:
        try:
            n = asyncio.run(memory.remember_in_cognee(recorded))
            report["memory"]["cognee"] = f"stored {n} finding(s)"
        except Exception as e:  # Cognee is an optional extra, never a reason to fail the run
            report["memory"]["cognee"] = f"skipped: {type(e).__name__}: {str(e)[:150]}"
    report["outcome"] = "INVESTIGATED"
    return _finish(report, out_dir, t0)


def _finish(report, out_dir, t0):
    report["seconds"] = round(time.time() - t0, 1)
    os.makedirs(out_dir, exist_ok=True)
    stem = "run_a_broken" if report["outcome"] == "STOPPED" else "run_b_clean"
    report["files"] = {"json": os.path.join(out_dir, f"{stem}.json"),
                       "markdown": os.path.join(out_dir, f"{stem}.md")}
    with open(report["files"]["json"], "w") as f:
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
        out.append(f"| {f['rank']} | {f['lead_id']} | `{_cell(f['segment'])}` | {_pct(f['contribution'])} | {_cell(f['verdict'])} |")

    for f in r["judgement"]["findings"]:
        out += ["", f"### #{f['rank']} {f['lead_id']} — {f['verdict']}", ""]
        if f["merged_into"]:
            out.append(f"Explains largely the same orders as **{f['merged_into']}** "
                       f"({f['overlap']:.0%} of its lost orders overlap), so it was merged rather than double-counted.")
            continue
        vr = f["volume_rate"] or {}
        out += [f"- **Accounts for** {_pct(f['contribution'])} of the total change "
                f"(starting lead: {_pct(f['lead_contribution'])})",
                f"- **Drill-down:** " + " → ".join(
                    [str(f["start_segment"])] + [f"{lvl['chosen']['dimension']}={lvl['chosen']['value']}"
                                                  for lvl in f["drill_path"] if lvl["chosen"]]),
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
    args = ap.parse_args()
    r = run(C.ORDERS_BROKEN_PATH if args.broken else C.ORDERS_PATH, remember=args.remember)
    print(f"health: {r['health']['status']}  outcome: {r['outcome']}  ({r['seconds']}s)")
    if r["outcome"] == "STOPPED":
        for c in r["health"]["checks"]:
            if not c["ok"]:
                print(f"  FAIL {c['name']}: {c['evidence']}")
    else:
        print(f"planner: {r['plan']['planner']}")
        for f in r["judgement"]["findings"]:
            print(f"  #{f['rank']} {f['lead_id']:24s} {_pct(f['contribution']):>6s}  {f['verdict']}")
        m = r["memory"]
        print(f"memory: {len(m['prior'])} prior finding(s) considered, {len(m['recorded'])} recorded"
              + (f", cognee {m['cognee']}" if m["cognee"] else ""))
    print(f"report: {r['files']['markdown']}")


if __name__ == "__main__":
    main()
