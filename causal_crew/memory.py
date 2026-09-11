"""Memory: verified findings become context for the next question.

Every SUPPORTED finding is appended to memory/findings.jsonl, and the planner
reads it back on the next run. Storing findings in Cognee as well is opt-in
(`run --remember`): each note costs ~45 seconds and many LLM calls, more than
Gemini's free tier allows during a live demo.
"""

import json
import os
from datetime import datetime, timezone

from causal_crew import config as C

MEMORY_PATH = os.path.join(os.path.dirname(C.CONTEXT_DIR), "memory", "findings.jsonl")


def recall(path=MEMORY_PATH):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _key(r):
    return r["question"], json.dumps(r["segment"], sort_keys=True), r["change_point"]


def write_back(findings, question, windows, path=MEMORY_PATH):
    """Append SUPPORTED findings that aren't already in memory; return the new records."""
    known = {_key(r) for r in recall(path)}
    new = []
    for f in findings:
        if not f["verdict"].startswith("SUPPORTED"):
            continue
        ctx = f.get("linked_context")
        rec = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "question": question, "windows": windows, "segment": f["segment"],
               "verdict": f["verdict"], "contribution": f["contribution"],
               "change_point": f["change_point"],
               "linked_context": {k: ctx[k] for k in ("title", "date", "file")} if ctx else None}
        if _key(rec) not in known:
            known.add(_key(rec))
            new.append(rec)
    if new:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in new)
    return new


def describe(r):
    seg = ", ".join(f"{k}={v}" for k, v in r["segment"].items())
    share = f"{r['contribution']:.0%}" if r.get("contribution") is not None else "an unknown share"
    ctx = (f"; lines up with '{r['linked_context']['title']}' ({r['linked_context']['date']})"
           if r.get("linked_context") else "")
    cur = r["windows"]["current"]
    return (f"{r['verdict']}: {seg} accounts for {share} of the change in {cur[0]}..{cur[1]}, "
            f"change point {r['change_point']}{ctx}.")


async def remember_in_cognee(records):
    """Opt-in: push records into Cognee's knowledge graph."""
    from dotenv import load_dotenv
    load_dotenv(C.ENV_PATH)
    import cognee
    for r in records:
        await cognee.remember("Verified Causal Crew finding. " + describe(r))
    return len(records)
