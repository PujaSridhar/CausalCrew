from datetime import date

import pandas as pd

from causal_crew.health import run


def _rewrite(path, fn, tmp_path, name):
    df = fn(pd.read_parquet(path))
    out = tmp_path / f"{name}.parquet"
    df.to_parquet(out)
    return str(out)


def test_clean_data_passes_despite_real_drop(orders):
    # West new-customer orders fall 60% (total -15%); that's business, not a data break
    r = run(orders)
    assert r["status"] == "PASS", r["failed"]


def test_duplicated_day_fails_and_names_the_day(orders, tmp_path):
    day = date(2026, 9, 3)
    broken = _rewrite(orders, lambda df: pd.concat([df, df[df.date == day]]), tmp_path, "dup")
    r = run(broken)
    assert r["status"] == "FAIL"
    by = {c["name"]: c for c in r["checks"]}
    assert by["duplicates"]["days"] == ["2026-09-03"]
    assert by["row_counts"]["days"] == ["2026-09-03"]


def test_stale_data_fails_freshness(orders):
    r = run(orders, expected_latest="2026-09-10")
    assert r["failed"] == ["freshness"]


def test_unit_change_fails_scale_break(orders, tmp_path):
    def cents(df):
        df = df.copy()
        df.loc[df.date >= date(2026, 8, 27), "revenue"] *= 100
        return df
    assert "scale_break" in run(_rewrite(orders, cents, tmp_path, "cents"))["failed"]


def test_null_spike_fails(orders, tmp_path):
    def nulls(df):
        df = df.copy()
        df["region"] = df["region"].astype(object)
        mask = (df.date >= date(2026, 8, 27)) & (df.index % 10 == 0)
        df.loc[mask, "region"] = None
        return df
    assert "null_spike" in run(_rewrite(orders, nulls, tmp_path, "nulls"))["failed"]
