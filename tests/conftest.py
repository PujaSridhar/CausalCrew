from datetime import date, timedelta

import pandas as pd
import pytest

STEP = date(2026, 8, 27)


@pytest.fixture
def orders(tmp_path):
    """Deterministic data: West new-customer orders fall 60% from STEP; nothing else moves."""
    rows, oid = [], 0
    day = date(2026, 6, 1)
    while day <= date(2026, 9, 9):
        for region in ("West", "East"):
            for ctype in ("new", "returning"):
                for channel in ("web", "app"):
                    for cat in ("a", "b"):
                        n = 4 if (region == "West" and ctype == "new" and day >= STEP) else 10
                        for _ in range(n):
                            oid += 1
                            rows.append((f"O{oid}", day, region, cat, channel, ctype, 1, 100.0))
        day += timedelta(days=1)
    path = tmp_path / "orders.parquet"
    pd.DataFrame(rows, columns=["order_id", "date", "region", "product_category",
                                "channel", "customer_type", "units", "revenue"]).to_parquet(path)
    return str(path)
