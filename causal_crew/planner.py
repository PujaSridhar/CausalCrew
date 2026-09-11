"""Planner: turn the question, the data, and nearby context into 3-4 leads.

Data-driven leads come straight from SQL (the largest single-dimension
deltas), so the context can't decide which leads exist. Gemini adds
context-driven leads and writes their hypotheses; it never produces numbers.
If Gemini fails, a keyword match between context notes and data values stands
in, so the demo path never depends on the LLM being up.
"""

import json
import os
import re
import urllib.request
from datetime import timedelta

import duckdb
from dotenv import load_dotenv

from causal_crew import config as C
from causal_crew.investigator import default_windows, load_context

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def lead_id(segment):
    raw = "_".join(f"{k}-{v}" for k, v in sorted(segment.items())).lower()
    return re.sub(r"[^a-z0-9_-]", "-", raw)[:64]


def data_driven_leads(orders_path, windows, k=C.PLANNER_DATA_LEADS):
    """Largest single-dimension deltas, in the direction of the total change."""
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    delta_sql = ("coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) - "
                 "coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0)")
    with duckdb.connect() as con:
        total = con.execute(f"SELECT {delta_sql} FROM read_parquet(?)",
                            [c0, c1, b0, b1, orders_path]).fetchone()[0]
        values, rows = {}, []
        for dim in C.DIMENSIONS:
            df = con.execute(f"SELECT {dim} AS value, {delta_sql} AS delta FROM read_parquet(?) "
                             f"GROUP BY {dim}", [c0, c1, b0, b1, orders_path]).df()
            values[dim] = sorted(str(v) for v in df.value)
            rows += [(dim, str(r.value), float(r.delta)) for r in df.itertuples()]
    sign = 1.0 if total >= 0 else -1.0
    top = sorted(rows, key=lambda r: r[2] * sign, reverse=True)[:k]
    leads = [{"lead_id": lead_id({d: v}), "segment": {d: v}, "source": "data", "event_file": None,
              "hypothesis": f"{d} = {v} has one of the largest revenue changes between the windows."}
             for d, v, _ in top]
    return leads, values, float(total)


def context_near(events, windows):
    lo = windows["current"][0] - timedelta(days=C.PLANNER_CONTEXT_DAYS)
    hi = windows["current"][1]
    return [e for e in events if lo <= e["date"] <= hi]


def _prompt(question, values, data_leads, events):
    notes = "\n\n".join(f"[{e['file']}] {e['date']} — {e['title']}\n{e['text'][:700]}" for e in events)
    taken = [lead["segment"] for lead in data_leads]
    return f"""You are planning an investigation into a business metric change.

Question: {question}

Dimensions and their allowed values:
{json.dumps(values, indent=2)}

Leads already chosen from the data (do not repeat these): {json.dumps(taken)}

Company context notes near the change:
{notes}

Propose up to 2 additional leads, each grounded in one context note that could
plausibly relate to the change. A lead is a segment of 1 or 2 dimensions using
only the allowed values above. Do not include any numbers. Phrase each
hypothesis as a possibility to test ("may", "could", "lines up with"), never
as a conclusion.

Return JSON only, in this shape:
{{"leads": [{{"segment": {{"<dimension>": "<value>"}}, "hypothesis": "<one sentence>", "event_file": "<file name>"}}]}}"""


def ask_gemini(prompt):
    load_dotenv(C.ENV_PATH)
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = (os.environ.get("PLANNER_MODEL") or C.PLANNER_MODEL).split("/", 1)[-1]
    if not key:
        raise RuntimeError("LLM_API_KEY is not set")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    req = urllib.request.Request(GEMINI_URL.format(model=model), data=json.dumps(body).encode(),
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=C.GEMINI_TIMEOUT_S) as r:
        data = json.load(r)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def validate(candidates, values, taken, event_files):
    """Keep only well-formed leads on real dimension values that aren't duplicates."""
    out, seen = [], {frozenset(s.items()) for s in taken}
    for c in candidates:
        seg = c.get("segment") if isinstance(c, dict) else None
        if not isinstance(seg, dict) or not 1 <= len(seg) <= 2:
            continue
        seg = {str(k): str(v) for k, v in seg.items()}
        if any(k not in values or v not in values[k] for k, v in seg.items()):
            continue
        if frozenset(seg.items()) in seen:
            continue
        seen.add(frozenset(seg.items()))
        ef = c.get("event_file")
        out.append({"lead_id": lead_id(seg), "segment": seg, "source": "context",
                    "event_file": ef if ef in event_files else None,
                    "hypothesis": str(c.get("hypothesis", ""))[:300]})
    return out


def keyword_leads(events, values, taken):
    """Fallback: a context note whose title names a data value becomes a lead on it.

    Titles only: bodies are too loose (the shipping-fee note names East only to
    say it is unchanged)."""
    cands = []
    for e in events:
        text = e["title"].lower()
        for dim, vals in values.items():
            for v in vals:
                if re.search(rf"\b{re.escape(v.lower())}\b", text):
                    cands.append({"segment": {dim: v}, "event_file": e["file"],
                                  "hypothesis": f"{e['title']} ({e['date']}) mentions {dim} = {v}."})
    return validate(cands, values, taken, {e["file"] for e in events})


def plan(question=C.DEMO_QUESTION, orders_path=C.ORDERS_PATH, windows=None, events=None, ask=ask_gemini):
    windows = windows or default_windows()
    events = context_near(load_context() if events is None else events, windows)
    data_leads, values, total = data_driven_leads(orders_path, windows)
    taken = [lead["segment"] for lead in data_leads]
    room = C.PLANNER_MAX_LEADS - len(data_leads)

    planner, error = "gemini", None
    try:
        parsed = json.loads(ask(_prompt(question, values, data_leads, events)))
        extra = validate(parsed.get("leads", []), values, taken, {e["file"] for e in events})
    except Exception as e:  # any LLM failure falls back to the deterministic path
        planner, error = "fallback", f"{type(e).__name__}: {str(e)[:200]}"
        extra = keyword_leads(events, values, taken)
    if planner == "gemini" and not extra:
        planner = "gemini+fallback"
        extra = keyword_leads(events, values, taken)

    return {"question": question, "total_delta": round(total, 2), "planner": planner, "error": error,
            "context_considered": [e["file"] for e in events],
            "leads": data_leads + extra[:room]}
