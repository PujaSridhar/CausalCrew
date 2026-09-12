"""Generate the Causal Crew demo orders dataset.

Deterministic under SEED. Writes the clean dataset and a broken copy.

Planted real cause
    The West flat shipping fee rises $4.99 -> $8.99 on SHIPPING_FEE_DATE (D).
    West orders fall about 30% after it, concentrated in new customers
    (new -55%, returning -12%), evenly across categories and channels. The
    new customers who still order build slightly bigger baskets.

Planted decoy
    The annual summer email campaign ends on 2026-08-25 (D-2). Email orders
    dip in every region. The same dip happened when the 2025 campaign ended,
    so the seasonality check should reject it. West customers rarely use
    email, so the West drop barely touches the email channel.

Broken copy (demo run A)
    orders_broken has every row of DUPLICATED_DAY loaded twice, with the
    same order_ids, matching the DATA-114 re-run note in context/.
"""

import os
from datetime import date

import numpy as np
import pandas as pd

# Every seed in an 8-seed sweep passed verify_orders.py. 77 is the default
# because its headline sits closest to the spec's "revenue dropped ~15%".
SEED = int(os.environ.get("SEED", 77))
HERE = os.path.dirname(os.path.abspath(__file__))

START = date(2025, 8, 1)
END = date(2026, 9, 9)
SHIPPING_FEE_DATE = date(2026, 8, 27)
DUPLICATED_DAY = date(2026, 9, 3)

BASE_DAILY_ORDERS = 1500
YOY_GROWTH = 0.08
# Common day-to-day demand swing on top of Poisson noise.
DAILY_DEMAND_SD = 0.02

REGIONS = {"West": 0.32, "East": 0.28, "South": 0.22, "North": 0.18}
CHANNELS = {"web": 0.50, "app": 0.35, "email": 0.15}
# West customers skew to the app, so email is a small channel there. This
# keeps the West shipping-fee drop from leaking into the email decoy.
WEST_CHANNELS = {"web": 0.52, "app": 0.43, "email": 0.05}
CUSTOMER_TYPES = {"new": 0.42, "returning": 0.58}
WEST_CUSTOMER_TYPES = {"new": 0.45, "returning": 0.55}
CATEGORIES = {"electronics": 0.18, "apparel": 0.24, "home": 0.20,
              "beauty": 0.16, "grocery": 0.22}
UNIT_PRICE = {"electronics": 142.0, "apparel": 48.0, "home": 67.0,
              "beauty": 31.0, "grocery": 22.0}
UNITS = np.array([1, 2, 3, 4])
UNITS_P = np.array([0.50, 0.28, 0.14, 0.08])

EMAIL_CAMPAIGN_LIFT = 1.20
WEST_FEE_EFFECT = {"new": 0.45, "returning": 0.88}
WEST_NEW_BASKET_BUMP = 1.04


def email_campaign_active(d):
    # runs Jun 15 through Aug 24 every year; Aug 25 is the first day without
    return (6, 15) <= (d.month, d.day) <= (8, 24)


def build(rng):
    parts = []
    days = pd.date_range(START, END, freq="D")

    for i, ts in enumerate(days):
        d = ts.date()
        base = BASE_DAILY_ORDERS * (1 + YOY_GROWTH * i / 365) * rng.lognormal(0, DAILY_DEMAND_SD)
        email_lift = EMAIL_CAMPAIGN_LIFT if email_campaign_active(d) else 1.0
        fee_live = d >= SHIPPING_FEE_DATE

        for region, rw in REGIONS.items():
            ctypes = WEST_CUSTOMER_TYPES if region == "West" else CUSTOMER_TYPES
            channels = WEST_CHANNELS if region == "West" else CHANNELS
            for channel, chw in channels.items():
                for ctype, ctw in ctypes.items():
                    for cat, catw in CATEGORIES.items():
                        mult = email_lift if channel == "email" else 1.0
                        west_hit = region == "West" and fee_live
                        if west_hit:
                            mult *= WEST_FEE_EFFECT[ctype]

                        n = rng.poisson(base * rw * chw * ctw * catw * mult)
                        if n == 0:
                            continue

                        units = rng.choice(UNITS, size=n, p=UNITS_P)
                        revenue = UNIT_PRICE[cat] * units * rng.lognormal(0, 0.25, n)
                        if west_hit and ctype == "new":
                            revenue *= WEST_NEW_BASKET_BUMP

                        parts.append(pd.DataFrame({
                            "date": d,
                            "region": region,
                            "product_category": cat,
                            "channel": channel,
                            "customer_type": ctype,
                            "units": units,
                            "revenue": np.round(revenue, 2),
                        }))

    df = pd.concat(parts, ignore_index=True)
    # shuffle within each day so order ids don't encode the segment
    df = df.sample(frac=1.0, random_state=SEED).sort_values("date", kind="stable")
    df.insert(0, "order_id", [f"ORD-{k:07d}" for k in range(1, len(df) + 1)])
    return df.reset_index(drop=True)


def write(df, name, out=HERE, csv=True):
    df.to_parquet(os.path.join(out, f"{name}.parquet"), index=False)
    if csv:
        df.to_csv(os.path.join(out, f"{name}.csv"), index=False)
    print(f"wrote {len(df):,} rows -> {os.path.join(out, name)}.parquet" + (", .csv" if csv else ""))


def main(out=HERE):
    """Write to data/ by default; a Rote Play passes its run directory instead (parquet only)."""
    csv = out == HERE
    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(SEED)
    clean = build(rng)
    write(clean, "orders", out, csv)

    dup = clean[clean["date"] == DUPLICATED_DAY]
    broken = pd.concat([clean, dup], ignore_index=True).sort_values("date", kind="stable")
    write(broken.reset_index(drop=True), "orders_broken", out, csv)
    print(f"broken copy: {len(dup):,} rows of {DUPLICATED_DAY} loaded twice")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else HERE)
