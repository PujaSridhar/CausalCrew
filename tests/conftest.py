import random
from datetime import date, timedelta

import pandas as pd
import pytest

STEP = date(2026, 8, 27)
START = date(2025, 8, 1)   # a year of history so the seasonality check has data
END = date(2026, 9, 9)


def make_orders(path, seasonal_decoy=False):
    """Deterministic orders. West new-customer orders fall 60% from STEP.
    With seasonal_decoy, East app orders dip 30% from Aug 25 every year."""
    rows, oid, day, rng = [], 0, START, random.Random(7)
    while day <= END:
        late_summer = (day.month == 8 and day.day >= 25) or day.month == 9
        for region in ("West", "East"):
            for ctype in ("new", "returning"):
                for channel in ("web", "app"):
                    for cat in ("a", "b"):
                        n = 10
                        if region == "West" and ctype == "new" and day >= STEP:
                            n = 4
                        if seasonal_decoy and region == "East" and channel == "app" and late_summer:
                            n = 7
                        for _ in range(n):
                            oid += 1
                            rows.append((f"O{oid}", day, region, cat, channel, ctype, rng.randint(1, 4), 100.0))
        day += timedelta(days=1)
    pd.DataFrame(rows, columns=["order_id", "date", "region", "product_category",
                                "channel", "customer_type", "units", "revenue"]).to_parquet(path)
    return str(path)


@pytest.fixture(scope="session")
def orders(tmp_path_factory):
    return make_orders(tmp_path_factory.mktemp("data") / "orders.parquet")


@pytest.fixture(scope="session")
def orders_decoy(tmp_path_factory):
    return make_orders(tmp_path_factory.mktemp("data") / "orders_decoy.parquet", seasonal_decoy=True)
