"""Planner: turn the question, the data, and nearby context into 3-4 leads.

Data-driven leads come straight from SQL (the largest single-dimension
deltas), so the context can't decide which leads exist. Gemini, run as a
RocketRide pipeline with direct Gemini as backup, adds context-driven leads
and writes their hypotheses; it never produces numbers. If no LLM answers, a
keyword match on changelog titles stands in, so the demo never depends on one.
Notes whose title names a segment always become leads, so a relevant note
(the email campaign ending, say) can't be dropped by the LLM's choices.
"""

import json
import re
from datetime import timedelta

import duckdb

from causal_crew import config as C
from causal_crew import memory
from causal_crew.investigator import default_windows, load_context
from causal_crew.llm import parse_json, planner_llm


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


# The reply shape the planner asks for (single braces: this is not an f-string).
_LEADS_SHAPE = ('{"leads": [{"segment": {"<dimension>": "<value>"}, '
                '"hypothesis": "<one sentence>", "event_file": "<file name>"}]}')


def _prompt(question, values, data_leads, events, prior=()):
    notes = "\n\n".join(f"[{e['file']}] {e['date']} — {e['title']}\n{e['text'][:700]}" for e in events)
    taken = [lead["segment"] for lead in data_leads]
    remembered = "\n".join(f"- {p}" for p in prior) or "- none yet"
    return f"""You are planning an investigation into a business metric change.

Question: {question}

Dimensions and their allowed values:
{json.dumps(values, indent=2)}

Leads already chosen from the data (do not repeat these): {json.dumps(taken)}

Previously verified findings from memory (inform your leads; don't just repeat them):
{remembered}

Company context notes near the change:
{notes}

Propose up to 2 additional leads, each grounded in one context note that could
plausibly relate to the change. A lead is a segment of 1 or 2 dimensions using
only the allowed values above. Do not include any numbers. Phrase each
hypothesis as a possibility to test ("may", "could", "lines up with"), never
as a conclusion.

Return JSON only, in this shape:
{_LEADS_SHAPE}"""


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
        title = e["title"].lower()
        for dim, vals in values.items():
            for v in vals:
                if re.search(rf"\b{re.escape(v.lower())}\b", title):
                    cands.append({"segment": {dim: v}, "event_file": e["file"],
                                  "hypothesis": f"'{e['title']}' ({e['date']}) names {dim} = {v}; "
                                                "the change may line up with it."})
    return validate(cands, values, taken, {e["file"] for e in events})


def plan(question=C.DEMO_QUESTION, orders_path=C.ORDERS_PATH, windows=None, events=None, ask=None,
         memory_records=None):
    windows = windows or default_windows()
    ask = ask or planner_llm()
    records = memory.recall() if memory_records is None else memory_records
    prior = [memory.describe(r) for r in records][-5:]
    events = context_near(load_context() if events is None else events, windows)
    data_leads, values, total = data_driven_leads(orders_path, windows)
    taken = [lead["segment"] for lead in data_leads]
    room = C.PLANNER_MAX_LEADS - len(data_leads)

    files = {e["file"] for e in events}
    error = None
    try:
        parsed = parse_json(ask(_prompt(question, values, data_leads, events, prior)))
        llm_leads = validate(parsed.get("leads", []), values, taken, files)
        planner = getattr(ask, "engine", None) or "llm"
    except Exception as e:  # any LLM failure falls back to the deterministic path
        planner, error, llm_leads = "fallback", f"{type(e).__name__}: {str(e)[:200]}", []
    title_leads = keyword_leads(events, values, taken)
    extra = validate(title_leads + llm_leads, values, taken, files)  # dedupe across both

    return {"question": question, "total_delta": round(total, 2), "planner": planner, "error": error,
            "context_considered": [e["file"] for e in events], "memory_considered": prior,
            "leads": data_leads + extra[:room]}
