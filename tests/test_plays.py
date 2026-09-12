import json
from datetime import date

from conftest import STEP

from causal_crew import plays
from causal_crew.investigator import investigate

WINDOWS = {"baseline": (date(2026, 8, 13), date(2026, 8, 26)), "current": (date(2026, 8, 27), date(2026, 9, 9))}
EVENTS = [{"date": date(2026, 8, 27), "title": "West shipping fee", "file": "b.md",
           "text": "Shipping fee for West orders rises."}]
RECIPE = {"lead_id": "west", "hypothesis": "West shipping fee", "start_segment": {"region": "West"},
          "path": [{"dimension": "customer_type", "value": "new"}]}


def test_latest_windows_end_at_the_data(orders):
    assert plays.latest_windows(orders) == WINDOWS


def test_health_play_passes_clean_data(orders):
    out = plays.health_play(orders, WINDOWS)
    assert out["status"] == "PASS" and out["trust"] == "OK"


def test_csv_input_is_converted_once(orders, tmp_path):
    import duckdb
    csv = str(tmp_path / "orders.csv")
    with duckdb.connect() as con:
        con.execute(f"COPY (SELECT * FROM read_parquet('{orders}')) TO '{csv}' (HEADER)")
    converted = plays.as_parquet(csv, str(tmp_path / "ws"))
    assert converted.endswith("orders.parquet")
    assert plays.health_play(converted, WINDOWS)["status"] == "PASS"
    assert plays.as_parquet(orders, str(tmp_path / "ws")) == orders


def test_main_exits_1_when_the_data_fails(orders, tmp_path, capsys):
    assert plays.main(["health", "--orders", orders, "--workspaces", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["trust"] == "OK"


def test_recipe_keeps_only_the_chosen_steps(orders, tmp_path):
    f = investigate("west", {"region": "West"}, orders_path=orders, events=EVENTS, root=str(tmp_path))
    f.update(verdict="SUPPORTED", contribution=f["final_contribution"])
    r = plays.recipe_from(f, "why?", {"current": ["2026-08-27", "2026-09-09"]})
    assert r["start_segment"] == {"region": "West"}
    assert r["path"] == [{"dimension": "customer_type", "value": "new"}]
    json.dumps(r)


def test_replay_follows_the_recipe_without_an_llm(orders, tmp_path):
    out = plays.replay_play(RECIPE, orders, WINDOWS, root=str(tmp_path), events=EVENTS)
    assert out["llm_calls"] == 0 and out["outcome"] == "REPLAYED"
    assert out["segment"] == {"region": "West", "customer_type": "new"}
    assert out["change_point"] == STEP.isoformat()
    assert out["path_still_supported"] is True


def test_replay_flags_a_path_the_data_no_longer_supports(orders, tmp_path):
    stale = {**RECIPE, "path": [{"dimension": "channel", "value": "web"}]}
    out = plays.replay_play(stale, orders, WINDOWS, root=str(tmp_path), events=EVENTS)
    assert out["path_still_supported"] is False


def test_replay_stops_on_broken_data(orders, tmp_path):
    import duckdb
    broken = str(tmp_path / "broken.parquet")
    with duckdb.connect() as con:
        con.execute(f"COPY (SELECT * FROM read_parquet('{orders}') UNION ALL "
                    f"SELECT * FROM read_parquet('{orders}') WHERE date = DATE '2026-09-03') TO '{broken}'")
    out = plays.replay_play(RECIPE, broken, WINDOWS, root=str(tmp_path / "ws"), events=EVENTS)
    assert out["outcome"] == "STOPPED" and "duplicates" in out["failed"]
