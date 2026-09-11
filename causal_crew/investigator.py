"""Investigator: one lead, one workspace, the spec's five steps.

Every number comes from SQL run in the lead's own workspace. Python only does
arithmetic on query results (the change-point split and the volume/rate
decomposition). No LLM here yet: the drill-down path follows the data.
"""

import glob
import os
import re
from datetime import date, timedelta

import numpy as np

from causal_crew import config as C
from causal_crew.workspace import Workspace


def default_windows():
    return {"baseline": tuple(date.fromisoformat(d) for d in C.DEMO_BASELINE_WINDOW),
            "current": tuple(date.fromisoformat(d) for d in C.DEMO_CURRENT_WINDOW)}


def _where(segment):
    """SQL filter for a segment. Dimension names are checked against config;
    values are always bound as parameters."""
    for dim in segment:
        if dim not in C.DIMENSIONS:
            raise ValueError(f"unknown dimension: {dim!r}")
    if not segment:
        return "TRUE", []
    return " AND ".join(f"{dim} = ?" for dim in segment), list(segment.values())


def _window_params(windows):
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    return [b0, b1, c0, c1]


def _totals(ws, table, segment, windows):
    where, params = _where(segment)
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
    where, params = _where(segment)
    return ws.sql(f"""
        SELECT {dim} AS value,
               coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS base_rev,
               coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS cur_rev
        FROM {table} WHERE {where} GROUP BY {dim}""", _window_params(windows) + params)


def _drill(ws, table, segment, windows):
    """Descend while one sub-segment carries a disproportionate share of the delta."""
    seg, path = dict(segment), []
    for level in range(1, C.MIN_DRILL_DEPTH + 1):
        remaining = [d for d in C.DIMENSIONS if d not in seg]
        if not remaining:
            break
        parent = _totals(ws, table, seg, windows)
        sign = np.sign(parent["delta"]) or -1.0
        examined, best = {}, None
        for dim in remaining:
            df = _breakdown(ws, table, seg, dim, windows)
            df["delta"] = df.cur_rev - df.base_rev
            top = df.loc[(df.delta * sign).idxmax()]
            delta_share = float(top.delta / parent["delta"]) if parent["delta"] else 0.0
            base_share = float(top.base_rev / parent["base_rev"]) if parent["base_rev"] else 0.0
            conc = delta_share / base_share if base_share else 0.0
            examined[dim] = {"top_value": str(top.value), "delta_share": round(delta_share, 4),
                             "base_share": round(base_share, 4), "concentration": round(conc, 3)}
            if best is None or conc > best["concentration"]:
                best = {"dimension": dim, "value": str(top.value), "concentration": round(conc, 3),
                        "delta_share": round(delta_share, 4)}
        narrowed = best["concentration"] >= C.DRILL_MIN_CONCENTRATION
        path.append({"level": level, "segment": dict(seg), "examined": examined,
                     "chosen": best if narrowed else None,
                     "note": None if narrowed else "effect is spread evenly; no sub-segment stands out"})
        if not narrowed:
            break
        seg[best["dimension"]] = best["value"]
    return seg, path


def _change_point(ws, table, segment, windows):
    where, params = _where(segment)
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
    return s.date.iloc[k].date() if hasattr(s.date.iloc[k], "date") else s.date.iloc[k], \
        float(v[k:].mean() - v[:k].mean())


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
        text = open(path).read()
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
                orders_path=C.ORDERS_PATH, events=None, root=C.WORKSPACE_DIR):
    windows = windows or default_windows()
    events = load_context() if events is None else events
    where, params = _where(segment)

    with Workspace(lead_id, orders_path=orders_path, root=root) as ws:
        total = _totals(ws, "orders", {}, windows)
        ws.sql(f"CREATE TABLE lead_orders AS SELECT * FROM orders WHERE {where}", params)

        # 1. size the lead
        lead = _totals(ws, "lead_orders", {}, windows)
        # 2. drill down
        final_seg, path = _drill(ws, "lead_orders", segment, windows)
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
