"""One isolated database per investigator.

Each investigator gets its own database holding a private copy of the orders
table plus whatever scratch tables it creates. Every SQL statement it runs is
recorded, so the report can show the query behind every number.

Backend: a local DuckDB file per investigator. If Hotdata access comes
through, a Hotdata-backed class with the same methods slots in here.
"""

import os
import re

import duckdb

from causal_crew import config as C

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Workspace:
    def __init__(self, lead_id, orders_path=C.ORDERS_PATH, root=C.WORKSPACE_DIR):
        if not _SAFE_ID.match(lead_id):
            raise ValueError(f"lead_id must be 1-64 letters, digits, _ or -: {lead_id!r}")
        os.makedirs(root, exist_ok=True)
        self.lead_id = lead_id
        self.path = os.path.join(root, f"{lead_id}.duckdb")
        if os.path.exists(self.path):
            os.remove(self.path)  # every investigation starts from a clean workspace
        self.con = duckdb.connect(self.path)
        self.queries = []
        self.sql("CREATE TABLE orders AS SELECT * FROM read_parquet(?)", [orders_path])

    def sql(self, query, params=None):
        """Run a statement, record it, and return the result as a DataFrame."""
        self.queries.append(query.strip())
        rel = self.con.execute(query, params or [])
        return rel.df() if rel.description else None

    def tables(self):
        return sorted(self.con.execute("SHOW TABLES").df()["name"])

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
