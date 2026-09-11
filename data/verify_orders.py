"""Check the demo data against the spec before the build depends on it.

This is a throwaway oracle, not the product: it runs rough versions of the
health check and judge from CLAUDE.md over the planted leads and exits
non-zero if the demo would not read as designed. The real health check and
judge get written tomorrow as tested modules.
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from causal_crew import config as C  # noqa: E402

FEE_DATE = pd.Timestamp("2026-08-27")
EMAIL_END = pd.Timestamp("2026-08-25")
DUPLICATED_DAY = "2026-09-03"

# Guard rails on the demo story. Looser than the planted values so seed
# noise doesn't trip them, tight enough to catch a design that doesn't hold.
HEADLINE_RANGE = (-0.20, -0.08)
WEST_CONTRIBUTION_RANGE = (0.60, 1.00)
EMAIL_MAX_CONTRIBUTION = 0.30


def load(name):
    df = pd.read_parquet(os.path.join(HERE, f"{name}.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    return df


def window(df, w):
    lo, hi = pd.Timestamp(w[0]), pd.Timestamp(w[1])
    return df[(df.date >= lo) & (df.date <= hi)]


def year_earlier(w):
    return tuple(str((pd.Timestamp(x) - pd.DateOffset(years=1)).date()) for x in w)


def seg(df, filt):
    m = pd.Series(True, index=df.index)
    for k, v in filt.items():
        m &= df[k] == v
    return df[m]


# --- health check -----------------------------------------------------------

def check(name, ok, evidence, days=()):
    return {"name": name, "ok": bool(ok), "evidence": evidence, "days": list(days)}


def health(df):
    out = []
    latest = df.date.max()
    out.append(check("freshness", latest == pd.Timestamp(C.DEMO_EXPECTED_LATEST_DATE),
                     f"max(date) = {latest.date()}"))

    daily = df.groupby("date").size()
    cur_lo = pd.Timestamp(C.DEMO_CURRENT_WINDOW[0])
    trailing = daily[(daily.index < cur_lo) &
                     (daily.index >= cur_lo - pd.Timedelta(days=C.ROW_COUNT_TRAILING_DAYS))]
    med = trailing.median()
    mad = (trailing - med).abs().median() * 1.4826
    cur_daily = daily[daily.index >= cur_lo]
    z = (cur_daily - med) / mad
    rel = cur_daily / med - 1
    bad = (z.abs() > C.ROW_COUNT_ROBUST_Z) & (rel.abs() > C.ROW_COUNT_MIN_REL_DEV)
    worst = z.abs().idxmax()
    days = [str(d.date()) for d in z[bad].index]
    ev = f"worst day {worst.date()}: {rel[worst]:+.0%} vs trailing median, |z| = {abs(z[worst]):.2f}"
    out.append(check("row_counts", not days, ev, days))

    dup_mask = df.order_id.duplicated(keep=False)
    dup_days = sorted({str(d.date()) for d in df[dup_mask].date})
    out.append(check("duplicates", not dup_mask.any(),
                     f"{int(df.order_id.duplicated().sum())} duplicate order_ids" +
                     (f" on {', '.join(dup_days)}" if dup_days else ""), dup_days))

    base, cur = window(df, C.DEMO_BASELINE_WINDOW), window(df, C.DEMO_CURRENT_WINDOW)
    rise = max((cur[k].isna().mean() - base[k].isna().mean()) * 100 for k in C.KEY_COLUMNS)
    out.append(check("null_spike", rise <= C.NULL_SPIKE_MAX_PP, f"max null rise {rise:.2f}pp"))

    ratio = cur.revenue.median() / base.revenue.median()
    lo, hi = C.SCALE_BREAK_RATIO
    out.append(check("scale_break", lo <= ratio <= hi, f"median order value ratio {ratio:.3f}"))
    return out


def print_health(results):
    for r in results:
        print(f"  {r['name']:12s} {'PASS' if r['ok'] else 'FAIL'}  {r['evidence']}")


# --- investigator + judge approximations -------------------------------------

def delta(df, filt, cur_w, base_w):
    s = seg(df, filt)
    return window(s, cur_w).revenue.sum() - window(s, base_w).revenue.sum()


def bootstrap_ci(df, filt):
    s = seg(df, filt)
    cur = window(s, C.DEMO_CURRENT_WINDOW).groupby("date").revenue.sum().values
    base = window(s, C.DEMO_BASELINE_WINDOW).groupby("date").revenue.sum().values
    rng = np.random.default_rng(C.BOOTSTRAP_SEED)
    draws = [rng.choice(cur, len(cur)).sum() - rng.choice(base, len(base)).sum()
             for _ in range(C.BOOTSTRAP_ITERATIONS)]
    a = (1 - C.BOOTSTRAP_CI) / 2
    return np.quantile(draws, a), np.quantile(draws, 1 - a)


def change_point(df, filt):
    s = seg(df, filt)
    end = pd.Timestamp(C.DEMO_CURRENT_WINDOW[1])
    s = s[s.date > end - pd.Timedelta(days=C.CHANGE_POINT_LOOKBACK_DAYS)]
    series = s.groupby("date").revenue.sum()
    v, n, m = series.values, len(series), C.CHANGE_POINT_MIN_SEGMENT_DAYS
    scores = {k: abs(v[k:].mean() - v[:k].mean()) * np.sqrt(k * (n - k) / n)
              for k in range(m, n - m)}
    return series.index[max(scores, key=scores.get)]


def volume_rate(df, filt):
    s = seg(df, filt)
    b, c = window(s, C.DEMO_BASELINE_WINDOW), window(s, C.DEMO_CURRENT_WINDOW)
    nb, nc = len(b), len(c)
    ab, ac = b.revenue.mean(), c.revenue.mean()
    vol = (nc - nb) * ab
    rate = nc * (ac - ab)
    return nb, nc, vol / (vol + rate), ab, ac


def consistency(df, filt):
    s = seg(df, filt)
    agg_sign = np.sign(delta(df, filt, C.DEMO_CURRENT_WINDOW, C.DEMO_BASELINE_WINDOW))
    detail = {}
    for dim in C.DIMENSIONS:
        if dim in filt:
            continue
        base = window(s, C.DEMO_BASELINE_WINDOW).groupby(dim).revenue.sum()
        cur = window(s, C.DEMO_CURRENT_WINDOW).groupby(dim).revenue.sum()
        d = cur.reindex(base.index, fill_value=0) - base
        share = base[np.sign(d) == agg_sign].sum() / base.sum()
        detail[dim] = (share, (d / base).round(3).to_dict())
    return min(v[0] for v in detail.values()), detail


def judge(df, name, filt, event):
    total = delta(df, {}, C.DEMO_CURRENT_WINDOW, C.DEMO_BASELINE_WINDOW)
    d = delta(df, filt, C.DEMO_CURRENT_WINDOW, C.DEMO_BASELINE_WINDOW)
    contribution = d / total
    lo, hi = bootstrap_ci(df, filt)
    nb, nc, vol_share, ab, ac = volume_rate(df, filt)
    noise_ok = nb >= C.MIN_ORDERS_PER_WINDOW and nc >= C.MIN_ORDERS_PER_WINDOW and (hi < 0 or lo > 0)

    # compare percent changes so year-over-year growth doesn't shrink last year's effect
    s = seg(df, filt)
    this_pct = d / window(s, C.DEMO_BASELINE_WINDOW).revenue.sum()
    ly_base_w = year_earlier(C.DEMO_BASELINE_WINDOW)
    ly = delta(df, filt, year_earlier(C.DEMO_CURRENT_WINDOW), ly_base_w)
    ly_pct = ly / window(s, ly_base_w).revenue.sum()
    ly_ratio = ly_pct / this_pct if this_pct else 0.0
    season_ok = ly_ratio < C.SEASONALITY_MAX_RATIO

    cons_share, cons_detail = consistency(df, filt)
    cons_ok = cons_share >= C.CONSISTENCY_MIN_SHARE

    cp = change_point(df, filt)
    timing_ok = event is not None and abs((cp - event).days) <= C.TIMING_MAX_DAYS

    failed = [n for n, ok in (("noise", noise_ok), ("seasonality", season_ok),
                              ("consistency", cons_ok)) if not ok]
    if failed:
        verdict = f"REJECTED ({', '.join(failed)})"
    elif contribution >= C.SUPPORTED_MIN_CONTRIBUTION:
        verdict = "SUPPORTED" + (" + CONTEXT_LINKED" if timing_ok else "")
    else:
        verdict = "INSUFFICIENT_EVIDENCE"

    print(f"\n--- lead: {name}  {filt}")
    print(f"  delta ${d:,.0f} of total ${total:,.0f}  -> contribution {contribution:.1%}")
    print(f"  orders {nb:,} -> {nc:,} ({nc / nb - 1:+.1%}); AOV ${ab:.2f} -> ${ac:.2f}; "
          f"volume explains {vol_share:.0%} of the change")
    print(f"  noise:       CI95 [{lo:,.0f}, {hi:,.0f}]  {'PASS' if noise_ok else 'FAIL'}")
    print(f"  seasonality: this year {this_pct:+.1%}, last year {ly_pct:+.1%} "
          f"-> {ly_ratio:.0%} of this year's effect  "
          f"{'PASS' if season_ok else 'FAIL'}")
    print(f"  consistency: worst same-direction share {cons_share:.0%}  {'PASS' if cons_ok else 'FAIL'}")
    for dim, (share, rel) in cons_detail.items():
        print(f"      {dim:17s} {share:.0%}  {rel}")
    print(f"  change point {cp.date()}" +
          (f"; event {event.date()}; timing {'PASS' if timing_ok else 'FAIL'}" if event is not None else ""))
    print(f"  VERDICT: {verdict}")
    return {"verdict": verdict, "failed": failed, "contribution": contribution,
            "change_point": cp, "season_ratio": ly_ratio}


def overlap(df, a, b):
    """Share of lead b's lost orders that also sit inside lead a's segment."""
    def lost(filt):
        s = seg(df, filt)
        return len(window(s, C.DEMO_BASELINE_WINDOW)) - len(window(s, C.DEMO_CURRENT_WINDOW))
    return lost({**a, **b}) / lost(b)


def main():
    problems = []

    print("=== RUN A: broken data ===")
    broken = health(load("orders_broken"))
    print_health(broken)
    failed = {r["name"] for r in broken if not r["ok"]}
    if failed != {"duplicates", "row_counts"}:
        problems.append(f"broken run should fail exactly duplicates + row_counts, failed {sorted(failed)}")
    for r in broken:
        if r["name"] in ("duplicates", "row_counts") and r["days"] != [DUPLICATED_DAY]:
            problems.append(f"broken run {r['name']} should name only {DUPLICATED_DAY}, got {r['days']}")

    print("\n=== RUN B: clean data ===")
    df = load("orders")
    clean = health(df)
    print_health(clean)
    if not all(r["ok"] for r in clean):
        problems.append("clean run failed the health check")

    base = window(df, C.DEMO_BASELINE_WINDOW).revenue.sum()
    cur = window(df, C.DEMO_CURRENT_WINDOW).revenue.sum()
    headline = cur / base - 1
    print(f"\n  headline: revenue ${base:,.0f} -> ${cur:,.0f} ({headline:+.1%})")
    if not HEADLINE_RANGE[0] <= headline <= HEADLINE_RANGE[1]:
        problems.append(f"headline {headline:+.1%} outside {HEADLINE_RANGE}")

    print("\n  largest single-dimension deltas (what a data-driven planner would see):")
    rows = []
    for dim in C.DIMENSIONS:
        for val in df[dim].unique():
            rows.append((dim, val, delta(df, {dim: val}, C.DEMO_CURRENT_WINDOW, C.DEMO_BASELINE_WINDOW)))
    for dim, val, dv in sorted(rows, key=lambda r: r[2])[:5]:
        print(f"    {dim}={val:12s} ${dv:,.0f}")

    west = judge(df, "West (planted cause)", {"region": "West"}, FEE_DATE)
    west_new = judge(df, "West / new (drill-down endpoint)",
                     {"region": "West", "customer_type": "new"}, FEE_DATE)
    email = judge(df, "email channel (planted decoy)", {"channel": "email"}, EMAIL_END)
    new_all = judge(df, "new customers (data-driven lead)", {"customer_type": "new"}, None)

    ov = overlap(df, {"region": "West"}, {"customer_type": "new"})
    print(f"\n  overlap: {ov:.0%} of the new-customer lead's lost orders are West orders "
          f"-> {'MERGE' if ov > C.OVERLAP_MERGE_THRESHOLD else 'keep separate'}")

    for lead, r in (("West", west), ("West / new", west_new)):
        if r["verdict"] != "SUPPORTED + CONTEXT_LINKED":
            problems.append(f"{lead} should be SUPPORTED + CONTEXT_LINKED, got {r['verdict']}")
    lo, hi = WEST_CONTRIBUTION_RANGE
    if not lo <= west["contribution"] <= hi:
        problems.append(f"West contribution {west['contribution']:.0%} outside [{lo:.0%}, {hi:.0%}]")
    if "seasonality" not in email["failed"]:
        problems.append(f"email decoy should fail seasonality, got {email['verdict']}")
    if email["contribution"] >= EMAIL_MAX_CONTRIBUTION:
        problems.append(f"email contribution {email['contribution']:.0%} not low")
    if abs((email["change_point"] - EMAIL_END).days) > C.TIMING_MAX_DAYS:
        problems.append(f"email change point {email['change_point'].date()} not near {EMAIL_END.date()}")
    if ov <= C.OVERLAP_MERGE_THRESHOLD:
        problems.append("new-customer lead should merge into West")

    print(f"\nSUMMARY headline={headline:+.1%} west={west['contribution']:.0%} "
          f"email={email['contribution']:.0%} email_season={email['season_ratio']:.0%} "
          f"email_cp={email['change_point'].date()} new_customers={new_all['verdict']} "
          f"overlap={ov:.0%}")
    if problems:
        print("DEMO DATA DOES NOT READ AS DESIGNED:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("OK: demo data reads as designed.")


if __name__ == "__main__":
    main()
