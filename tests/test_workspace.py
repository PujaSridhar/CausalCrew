import datetime as dt

import pandas as pd
import pytest

from causal_crew.workspace import Workspace


@pytest.fixture
def orders(tmp_path):
    path = tmp_path / "orders.parquet"
    pd.DataFrame({
        "order_id": ["A", "B", "C"],
        "date": [dt.date(2026, 8, 1)] * 3,
        "region": ["West", "East", "West"],
        "revenue": [10.0, 20.0, 30.0],
    }).to_parquet(path)
    return str(path)


def test_loads_private_copy_of_orders(orders, tmp_path):
    with Workspace("west", orders_path=orders, root=str(tmp_path / "ws")) as ws:
        assert ws.sql("SELECT count(*) AS n FROM orders")["n"][0] == 3


def test_workspaces_do_not_share_scratch_tables(orders, tmp_path):
    root = str(tmp_path / "ws")
    with Workspace("lead_a", orders_path=orders, root=root) as a, \
         Workspace("lead_b", orders_path=orders, root=root) as b:
        a.sql("CREATE TABLE west_only AS SELECT * FROM orders WHERE region = 'West'")
        assert "west_only" in a.tables()
        assert "west_only" not in b.tables()


def test_every_statement_is_recorded(orders, tmp_path):
    with Workspace("west", orders_path=orders, root=str(tmp_path / "ws")) as ws:
        ws.sql("SELECT sum(revenue) FROM orders WHERE region = ?", ["West"])
        assert ws.queries[-1] == "SELECT sum(revenue) FROM orders WHERE region = ?"
        assert ws.queries[0].startswith("CREATE TABLE orders")


def test_rerun_starts_clean(orders, tmp_path):
    root = str(tmp_path / "ws")
    with Workspace("west", orders_path=orders, root=root) as ws:
        ws.sql("CREATE TABLE scratch AS SELECT 1 AS x")
    with Workspace("west", orders_path=orders, root=root) as ws:
        assert ws.tables() == ["orders"]


def test_rejects_unsafe_lead_id(orders, tmp_path):
    with pytest.raises(ValueError):
        Workspace("../escape", orders_path=orders, root=str(tmp_path / "ws"))
