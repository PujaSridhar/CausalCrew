"""Health check: can this number be trusted? Deterministic, no LLM.

Any failed check means "don't trust this number yet": the run stops and the
report names the check, the evidence, and the days involved.
"""

from datetime import date, timedelta

import duckdb
import numpy as np

from causal_crew import config as C
from causal_crew.investigator import default_windows


def _check(name, ok, evidence, days=(), sql=None):
    return {"name": name, "ok": bool(ok), "evidence": evidence, "days": list(days), "sql": sql}


def run(orders_path=C.ORDERS_PATH, windows=None, expected_latest=C.DEMO_EXPECTED_LATEST_DATE):
    windows = windows or default_windows()
    (b0, b1), (c0, c1) = windows["baseline"], windows["current"]
    expected = date.fromisoformat(expected_latest) if isinstance(expected_latest, str) else expected_latest
    checks = []

    with duckdb.connect() as con:
        con.read_parquet(orders_path).create_view("orders")

        # freshness
        q = "SELECT max(date) FROM orders"
        latest = con.execute(q).fetchone()[0]
        checks.append(_check("freshness", latest == expected,
                             f"max(date) = {latest}, expected {expected}", sql=q))

        # row counts: robust z vs the trailing window, with a relative floor
        t0 = c0 - timedelta(days=C.ROW_COUNT_TRAILING_DAYS)
        q = "SELECT date, count(*) AS n FROM orders WHERE date BETWEEN ? AND ? GROUP BY date ORDER BY date"
        daily = con.execute(q, [t0, c1]).df()
        trailing = daily[daily.date.dt.date < c0].n.to_numpy(float)
        current = daily[daily.date.dt.date >= c0]
        med = float(np.median(trailing))
        mad = float(np.median(np.abs(trailing - med))) * 1.4826
        flagged, worst = [], (None, 0.0, 0.0)
        for d, n in zip(current.date.dt.date, current.n.to_numpy(float)):
            rel = n / med - 1
            z = (n - med) / mad if mad else (0.0 if n == med else float("inf"))
            if abs(rel) > abs(worst[1]):
                worst = (d, rel, z)
            if abs(z) > C.ROW_COUNT_ROBUST_Z and abs(rel) > C.ROW_COUNT_MIN_REL_DEV:
                flagged.append(d.isoformat())
        ev = (f"worst day {worst[0]}: {worst[1]:+.0%} vs trailing {C.ROW_COUNT_TRAILING_DAYS}-day "
              f"median {med:,.0f}, |z| = {abs(worst[2]):.1f}")
        checks.append(_check("row_counts", not flagged, ev, flagged, sql=q))

        # duplicates
        q = ("SELECT date, count(*) AS extra FROM (SELECT order_id, date, count(*) OVER "
             "(PARTITION BY order_id) AS c FROM orders) WHERE c > 1 GROUP BY date ORDER BY date")
        dups = con.execute(q).df()
        days = [d.isoformat() for d in dups.date.dt.date]
        n_dup = con.execute("SELECT count(*) - count(DISTINCT order_id) FROM orders").fetchone()[0]
        checks.append(_check("duplicates", n_dup == 0,
                             f"{n_dup:,} duplicate order_ids" + (f" on {', '.join(days)}" if days else ""),
                             days, sql=q))

        # null spikes on key columns
        cols = ", ".join(f"avg(CASE WHEN {c} IS NULL THEN 1.0 ELSE 0 END) AS {c}" for c in C.KEY_COLUMNS)
        q = f"SELECT {cols} FROM orders WHERE date BETWEEN ? AND ?"
        base = con.execute(q, [b0, b1]).df().iloc[0]
        cur = con.execute(q, [c0, c1]).df().iloc[0]
        rises = {c: (float(cur[c]) - float(base[c])) * 100 for c in C.KEY_COLUMNS}
        col, rise = max(rises.items(), key=lambda kv: kv[1])
        checks.append(_check("null_spike", rise <= C.NULL_SPIKE_MAX_PP,
                             f"largest null-rate rise: {col} {rise:+.2f}pp", sql=q))

        # scale break (cents vs dollars)
        q = "SELECT median(revenue) FROM orders WHERE date BETWEEN ? AND ?"
        mb = con.execute(q, [b0, b1]).fetchone()[0]
        mc = con.execute(q, [c0, c1]).fetchone()[0]
        ratio = mc / mb if mb else float("inf")
        lo, hi = C.SCALE_BREAK_RATIO
        checks.append(_check("scale_break", lo <= ratio <= hi,
                             f"median order value {mb:,.2f} -> {mc:,.2f} (ratio {ratio:.3f})", sql=q))

    failed = [c["name"] for c in checks if not c["ok"]]
    return {"status": "FAIL" if failed else "PASS", "failed": failed, "checks": checks}
