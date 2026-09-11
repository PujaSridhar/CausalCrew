"""Investigator: one lead, one workspace, the spec's five steps.

Every number comes from SQL run in the lead's own workspace; Python only does
arithmetic on query results. At each drill-down level the LLM (via RocketRide)
chooses among sub-segments that pass a statistical threshold, and a
deterministic rule is the guardrail and the fallback.
"""

import glob
import json
import os
import re
from datetime import date, timedelta

import numpy as np

from causal_crew import config as C
from causal_crew.llm import investigator_llm, parse_json
from causal_crew.segments import segment_filter
from causal_crew.workspace import Workspace


def default_windows():
    return {"baseline": tuple(date.fromisoformat(d) for d in C.DEMO_BASELINE_WINDOW),
            "current": tuple(date.fromisoformat(d) for d in C.DEMO_CURRENT_WINDOW)}


def _window_params(windows):
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    return [b0, b1, c0, c1]


def _totals(ws, table, segment, windows):
    where, params = segment_filter(segment)
    b0, b1, c0, c1 = _window_params(windows)
    row = ws.sql(f"""
        SELECT coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS base_rev,
               coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS cur_rev,
               count(CASE WHEN date BETWEEN ? AND ? THEN 1 END) AS base_orders,
               count(CASE WHEN date BETWEEN ? AND ? THEN 1 END) AS cur_orders
        FROM {table} WHERE {where}""", [b0, b1, c0, c1, b0, b1, c0, c1] + params).iloc[0]
    out = {k: float(row[k]) for k in ("base_rev", "cur_rev")}
    out.update({k: int(row[k]) for k in ("base_orders", "cur_orders")})
    out["delta"] = out["cur_rev"] - out["base_rev"]
    return out


def _breakdown(ws, table, segment, dim, windows):
    where, params = segment_filter(segment)
    return ws.sql(f"""
        SELECT {dim} AS value,
               coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS base_rev,
               coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS cur_rev
        FROM {table} WHERE {where} GROUP BY {dim}""", _window_params(windows) + params)


def _qualifying(candidates):
    return [c for c in candidates if c["concentration"] >= C.DRILL_MIN_CONCENTRATION]


def rule_decider(lead_id, hypothesis, segment, level, candidates):
    """Deterministic: narrow into the most concentrated qualifying sub-segment."""
    q = _qualifying(candidates)
    if not q:
        return {"dimension": None, "value": None, "decided_by": "rule",
                "reason": "no sub-segment carries a disproportionate share of the change"}
    best = max(q, key=lambda c: c["concentration"])
    return {"dimension": best["dimension"], "value": best["top_value"], "decided_by": "rule",
            "reason": "the most concentrated sub-segment above the threshold"}


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _decision_prompt(lead_id, hypothesis, segment, level, candidates):
    rows = "\n".join(
        f"- {c['dimension']} = {c['top_value']}: {c['delta_share']:.0%} of the change, "
        f"{c['base_share']:.0%} of baseline revenue, concentration {c['concentration']:.2f}"
        + (" (qualifies)" if c["concentration"] >= C.DRILL_MIN_CONCENTRATION else "")
        for c in candidates)
    return f"""You are an investigator agent drilling into one lead of a revenue change.

Lead: {lead_id}: {hypothesis}
Current segment: {json.dumps(segment)} (drill-down level {level})

For each remaining dimension, the sub-segment with the largest change (computed in SQL):
{rows}

A sub-segment qualifies when its concentration is at least {C.DRILL_MIN_CONCENTRATION}: it carries
more of the change than its share of revenue. Choose the qualifying dimension that best
narrows this lead, or null if none qualifies. Give a one-sentence reason in plain words.
Use only numbers shown above.

Return JSON only: {{"dimension": "<dimension or null>", "reason": "<one sentence>"}}"""


def llm_decider(lead_id, hypothesis, segment, level, candidates, ask=None):
    """The LLM chooses the path among statistically qualifying sub-segments.

    The rule stays the guardrail: an unsupported choice is overridden, an
    unavailable LLM falls back to the rule, and a rationale citing a number
    that isn't in the evidence is withheld. Level 1 always asks, so every
    investigator runs through RocketRide; deeper levels ask only when there is
    something to choose.
    """
    rule = rule_decider(lead_id, hypothesis, segment, level, candidates)
    qualifying = {c["dimension"]: c for c in _qualifying(candidates)}
    if level > 1 and not qualifying:
        return rule
    prompt = _decision_prompt(lead_id, hypothesis, segment, level, candidates)
    ask = ask or investigator_llm(lead_id)
    try:
        reply = parse_json(ask(prompt))
    except Exception as e:
        return {**rule, "reason": f"{rule['reason']} (LLM unavailable: {type(e).__name__})",
                "engine_errors": list(getattr(ask, "errors", []) or [str(e)[:200]])}
    engine = getattr(ask, "engine", None) or "llm"
    errors = list(getattr(ask, "errors", []) or [])  # engines that failed before this one answered
    dim = reply.get("dimension")
    dim = None if dim in (None, "", "null", "none") else str(dim)
    reason = str(reply.get("reason", "")).strip()[:240]
    if not set(_NUMBER.findall(reason)) <= set(_NUMBER.findall(prompt)):
        reason = "(rationale withheld: it cited a number not in the evidence)"
    if dim is None and not qualifying:
        return {"dimension": None, "value": None, "decided_by": engine, "reason": reason,
                "engine_errors": errors}
    if dim in qualifying:
        return {"dimension": dim, "value": qualifying[dim]["top_value"], "decided_by": engine,
                "reason": reason, "engine_errors": errors}
    return {**rule, "decided_by": f"rule (overrode {engine})", "engine_errors": errors,
            "reason": f"{engine} chose {dim!r}, which the evidence doesn't support; took {rule['reason']}"}


def _drill(ws, table, segment, windows, decide, lead_id, hypothesis):
    """Descend while a sub-segment carries a disproportionate share of the delta."""
    seg, path = dict(segment), []
    for level in range(1, C.MIN_DRILL_DEPTH + 1):
        remaining = [d for d in C.DIMENSIONS if d not in seg]
        if not remaining:
            break
        parent = _totals(ws, table, seg, windows)
        sign = np.sign(parent["delta"]) or -1.0
        candidates = []
        for dim in remaining:
            df = _breakdown(ws, table, seg, dim, windows)
            df["delta"] = df.cur_rev - df.base_rev
            top = df.loc[(df.delta * sign).idxmax()]
            delta_share = float(top.delta / parent["delta"]) if parent["delta"] else 0.0
            base_share = float(top.base_rev / parent["base_rev"]) if parent["base_rev"] else 0.0
            candidates.append({"dimension": dim, "top_value": str(top.value),
                               "delta_share": round(delta_share, 4), "base_share": round(base_share, 4),
                               "concentration": round(delta_share / base_share if base_share else 0.0, 3)})
        d = decide(lead_id, hypothesis, dict(seg), level, candidates)
        path.append({"level": level, "segment": dict(seg), "candidates": candidates,
                     "chosen": {"dimension": d["dimension"], "value": d["value"]} if d["dimension"] else None,
                     "decided_by": d["decided_by"], "reason": d["reason"],
                     "engine_errors": d.get("engine_errors", [])})
        if not d["dimension"]:
            break
        seg[d["dimension"]] = d["value"]
    return seg, path


def _change_point(ws, table, segment, windows):
    where, params = segment_filter(segment)
    end = windows["current"][1]
    start = end - timedelta(days=C.CHANGE_POINT_LOOKBACK_DAYS - 1)
    ws.sql("DROP TABLE IF EXISTS daily_series")
    ws.sql(f"""
        CREATE TABLE daily_series AS
        SELECT date, sum(revenue) AS revenue, count(*) AS orders
        FROM {table} WHERE {where} AND date BETWEEN ? AND ?
        GROUP BY date ORDER BY date""", params + [start, end])
    s = ws.sql("SELECT date, revenue FROM daily_series ORDER BY date")
    v, n, m = s.revenue.to_numpy(float), len(s), C.CHANGE_POINT_MIN_SEGMENT_DAYS
    if n < 2 * m + 1:
        return None, 0.0
    scores = [abs(v[k:].mean() - v[:k].mean()) * np.sqrt(k * (n - k) / n) for k in range(m, n - m)]
    k = m + int(np.argmax(scores))
    cp = s.date.iloc[k]
    return (cp.date() if hasattr(cp, "date") else cp), float(v[k:].mean() - v[:k].mean())


def _volume_rate(t):
    if not t["base_orders"] or not t["cur_orders"]:
        return None
    ab, ac = t["base_rev"] / t["base_orders"], t["cur_rev"] / t["cur_orders"]
    vol = (t["cur_orders"] - t["base_orders"]) * ab
    rate = t["cur_orders"] * (ac - ab)
    total = vol + rate
    return {"orders_base": t["base_orders"], "orders_current": t["cur_orders"],
            "aov_base": round(ab, 2), "aov_current": round(ac, 2),
            "volume_effect": round(vol, 2), "rate_effect": round(rate, 2),
            "volume_share": round(vol / total, 4) if total else None}


def load_context(context_dir=C.CONTEXT_DIR):
    """Changelog fallback: read dated markdown notes directly (spec step 6 fallback)."""
    events = []
    for path in sorted(glob.glob(os.path.join(context_dir, "*.md"))):
        with open(path) as f:
            text = f.read()
        d = re.search(r"^Date:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
        t = re.search(r"^#\s+(.+)$", text, re.M)
        if d:
            events.append({"date": date.fromisoformat(d.group(1)),
                           "title": t.group(1).strip() if t else os.path.basename(path),
                           "file": os.path.basename(path), "text": text})
    return events


def match_context(events, change_point, segment):
    """Pick the event nearest the change point, preferring ones that name the segment."""
    if change_point is None:
        return None
    near = [e for e in events if abs((e["date"] - change_point).days) <= C.TIMING_MAX_DAYS]
    if not near:
        return None
    terms = [str(v).lower() for v in segment.values()]

    def mentions(e):
        return sum(bool(re.search(rf"\b{re.escape(t)}\b", e["text"].lower())) for t in terms)

    best = max(near, key=lambda e: (mentions(e), -abs((e["date"] - change_point).days)))
    return {"title": best["title"], "date": best["date"].isoformat(), "file": best["file"],
            "days_from_change_point": (best["date"] - change_point).days,
            "segment_terms_mentioned": mentions(best)}


def investigate(lead_id, segment, hypothesis="", windows=None,
                orders_path=C.ORDERS_PATH, events=None, root=C.WORKSPACE_DIR, decide=rule_decider):
    windows = windows or default_windows()
    events = load_context() if events is None else events
    where, params = segment_filter(segment)

    with Workspace(lead_id, orders_path=orders_path, root=root) as ws:
        total = _totals(ws, "orders", {}, windows)
        ws.sql(f"CREATE TABLE lead_orders AS SELECT * FROM orders WHERE {where}", params)

        # 1. size the lead
        lead = _totals(ws, "lead_orders", {}, windows)
        # 2. drill down
        final_seg, path = _drill(ws, "lead_orders", segment, windows, decide, lead_id, hypothesis)
        final = _totals(ws, "lead_orders", {k: v for k, v in final_seg.items() if k not in segment}, windows)
        # 3. change point
        cp, shift = _change_point(ws, "lead_orders", {k: v for k, v in final_seg.items() if k not in segment}, windows)
        # 4. volume vs rate
        vr = _volume_rate(final)
        # 5. context match
        ctx = match_context(events, cp, final_seg)

        return {
            "lead_id": lead_id,
            "hypothesis": hypothesis,
            "start_segment": dict(segment),
            "final_segment": final_seg,
            "windows": {k: [d.isoformat() for d in v] for k, v in windows.items()},
            "total_delta": round(total["delta"], 2),
            "lead_delta": round(lead["delta"], 2),
            "lead_contribution": round(lead["delta"] / total["delta"], 4) if total["delta"] else None,
            "final_delta": round(final["delta"], 2),
            "final_contribution": round(final["delta"] / total["delta"], 4) if total["delta"] else None,
            "drill_path": path,
            "change_point": cp.isoformat() if cp else None,
            "change_point_shift_per_day": round(shift, 2),
            "volume_rate": vr,
            "linked_context": ctx,
            "tables": ws.tables(),
            "queries": list(ws.queries),
        }
