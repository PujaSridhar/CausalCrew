"""Judge: deterministic evidence checks, overlap merge, ranking, verdicts.

The LLM never writes a verdict. Every number comes from SQL over the orders
data, and the verdict rules are plain Python with unit tests.
"""

import duckdb
import numpy as np

from causal_crew import config as C
from causal_crew.investigator import default_windows
from causal_crew.segments import segment_filter


class _DB:
    def __init__(self, orders_path):
        self.con = duckdb.connect()
        self.con.read_parquet(orders_path).create_view("orders")
        self.queries = []

    def q(self, sql, params=()):
        self.queries.append(sql.strip())
        return self.con.execute(sql, list(params)).df()

    def close(self):
        self.con.close()


def _year_earlier(window):
    return tuple(d.replace(year=d.year - 1) for d in window)


def _daily(db, seg, window):
    where, params = segment_filter(seg)
    return db.q(f"SELECT date, sum(revenue) AS rev, count(*) AS n FROM orders "
                f"WHERE {where} AND date BETWEEN ? AND ? GROUP BY date", params + list(window))


def _revenue(db, seg, window):
    where, params = segment_filter(seg)
    return float(db.q(f"SELECT coalesce(sum(revenue), 0) AS r FROM orders "
                      f"WHERE {where} AND date BETWEEN ? AND ?", params + list(window)).r[0])


def _orders(db, seg, window):
    where, params = segment_filter(seg)
    return int(db.q(f"SELECT count(*) AS n FROM orders WHERE {where} AND date BETWEEN ? AND ?",
                    params + list(window)).n[0])


def bootstrap_ci(current_daily, baseline_daily, iters=C.BOOTSTRAP_ITERATIONS,
                 level=C.BOOTSTRAP_CI, seed=C.BOOTSTRAP_SEED):
    """CI of (sum current - sum baseline), resampling days within each window."""
    cur, base = np.asarray(current_daily, float), np.asarray(baseline_daily, float)
    rng = np.random.default_rng(seed)
    draws = (rng.choice(cur, (iters, len(cur))).sum(1) - rng.choice(base, (iters, len(base))).sum(1))
    a = (1 - level) / 2
    return float(np.quantile(draws, a)), float(np.quantile(draws, 1 - a))


def noise_check(db, seg, windows):
    cur, base = _daily(db, seg, windows["current"]), _daily(db, seg, windows["baseline"])
    n_cur, n_base = int(cur.n.sum()), int(base.n.sum())
    if cur.empty or base.empty:
        return {"ok": False, "evidence": "no orders in one of the windows"}
    lo, hi = bootstrap_ci(cur.rev, base.rev)
    enough = min(n_cur, n_base) >= C.MIN_ORDERS_PER_WINDOW
    ok = enough and (hi < 0 or lo > 0)
    return {"ok": ok, "ci95": [round(lo, 2), round(hi, 2)],
            "evidence": f"orders {n_base:,} -> {n_cur:,} (min {C.MIN_ORDERS_PER_WINDOW}); "
                        f"95% CI of delta [{lo:,.0f}, {hi:,.0f}]"}


def seasonality_check(db, seg, windows):
    def pct(base_w, cur_w):
        b = _revenue(db, seg, base_w)
        return (_revenue(db, seg, cur_w) - b) / b if b else None

    this = pct(windows["baseline"], windows["current"])
    last = pct(_year_earlier(windows["baseline"]), _year_earlier(windows["current"]))
    if not this:
        return {"ok": False, "evidence": "no change to compare"}
    if last is None:
        return {"ok": False, "evidence": "no data one year earlier, so seasonality can't be ruled out"}
    ratio = last / this
    return {"ok": ratio < C.SEASONALITY_MAX_RATIO, "ratio": round(ratio, 4),
            "evidence": f"this year {this:+.1%}, same windows last year {last:+.1%} "
                        f"({ratio:.0%} of this year's effect; fails at {C.SEASONALITY_MAX_RATIO:.0%})"}


def consistency_check(db, seg, windows):
    where, params = segment_filter(seg)
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    agg = _revenue(db, seg, windows["current"]) - _revenue(db, seg, windows["baseline"])
    sign = np.sign(agg) or -1.0
    shares = {}
    for dim in (d for d in C.DIMENSIONS if d not in seg):
        df = db.q(f"""SELECT {dim} AS value,
                        coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS base_rev,
                        coalesce(sum(CASE WHEN date BETWEEN ? AND ? THEN revenue END), 0) AS cur_rev
                      FROM orders WHERE {where} GROUP BY {dim}""", [b0, b1, c0, c1] + params)
        same = df[np.sign(df.cur_rev - df.base_rev) == sign].base_rev.sum()
        shares[dim] = round(float(same / df.base_rev.sum()) if df.base_rev.sum() else 0.0, 4)
    if not shares:
        return {"ok": True, "evidence": "no sub-segments left to check"}
    worst = min(shares, key=shares.get)
    return {"ok": shares[worst] >= C.CONSISTENCY_MIN_SHARE, "shares": shares,
            "evidence": f"worst split {worst}: {shares[worst]:.0%} of baseline revenue moves "
                        f"with the aggregate (needs {C.CONSISTENCY_MIN_SHARE:.0%})"}


def timing_check(finding):
    ctx = finding.get("linked_context")
    if not ctx:
        return {"ok": False, "evidence": "no context linked"}
    days = abs(ctx["days_from_change_point"])
    return {"ok": days <= C.TIMING_MAX_DAYS,
            "evidence": f"'{ctx['title']}' is {days} day(s) from the change point"}


def overlap(db, kept_seg, seg, windows):
    """Share of seg's lost orders that also sit inside kept_seg."""
    if any(k in seg and seg[k] != v for k, v in kept_seg.items()):
        return 0.0
    def lost(s):
        return _orders(db, s, windows["baseline"]) - _orders(db, s, windows["current"])
    lost_seg = lost(seg)
    if lost_seg <= 0:
        return 0.0
    return min(1.0, max(0.0, lost({**kept_seg, **seg}) / lost_seg))


def verdict(noise_ok, season_ok, consistency_ok, timing_ok, contribution):
    """The spec's verdict rules. Returns (label, failed_checks)."""
    failed = [n for n, ok in (("noise", noise_ok), ("seasonality", season_ok),
                              ("consistency", consistency_ok)) if not ok]
    if failed:
        return f"REJECTED ({', '.join(failed)})", failed
    if contribution is not None and contribution >= C.SUPPORTED_MIN_CONTRIBUTION:
        return "SUPPORTED" + (" + CONTEXT_LINKED" if timing_ok else ""), []
    return "INSUFFICIENT_EVIDENCE", []


def judge(findings, orders_path=C.ORDERS_PATH, windows=None):
    windows = windows or default_windows()
    db = _DB(orders_path)
    try:
        judged = []
        for f in findings:
            seg = f["final_segment"]
            checks = {"noise": noise_check(db, seg, windows),
                      "seasonality": seasonality_check(db, seg, windows),
                      "consistency": consistency_check(db, seg, windows),
                      "timing": timing_check(f)}
            label, failed = verdict(checks["noise"]["ok"], checks["seasonality"]["ok"],
                                    checks["consistency"]["ok"], checks["timing"]["ok"],
                                    f["final_contribution"])
            judged.append({"lead_id": f["lead_id"], "hypothesis": f.get("hypothesis", ""),
                           "start_segment": f["start_segment"], "segment": seg,
                           "contribution": f["final_contribution"],
                           "lead_contribution": f["lead_contribution"],
                           "change_point": f["change_point"], "volume_rate": f["volume_rate"],
                           "linked_context": f["linked_context"], "drill_path": f["drill_path"],
                           "checks": checks, "verdict": label, "failed": failed,
                           "merged_into": None, "overlap": None})

        # overlap merge: larger contributions absorb leads that explain the same orders
        order = sorted(range(len(judged)), key=lambda i: -abs(judged[i]["contribution"] or 0))
        kept = []
        for i in order:
            j = judged[i]
            for k in kept:
                ov = overlap(db, judged[k]["segment"], j["segment"], windows)
                if ov > C.OVERLAP_MERGE_THRESHOLD:
                    j.update(merged_into=judged[k]["lead_id"], overlap=round(ov, 4),
                             verdict=f"MERGED into {judged[k]['lead_id']}")
                    break
            else:
                kept.append(i)

        ranked = [judged[i] for i in order if not judged[i]["merged_into"]] + \
                 [judged[i] for i in order if judged[i]["merged_into"]]
        for rank, j in enumerate(ranked, 1):
            j["rank"] = rank
        return {"findings": ranked, "queries": db.queries}
    finally:
        db.close()
