from datetime import date

import pytest

from conftest import STEP

from causal_crew.investigator import investigate, match_context


EVENTS = [
    {"date": date(2026, 8, 25), "title": "Email campaign ends", "file": "a.md",
     "text": "The summer email campaign ended."},
    {"date": date(2026, 8, 27), "title": "West shipping fee", "file": "b.md",
     "text": "Shipping fee for West orders rises."},
]


def run(orders, tmp_path):
    return investigate("west", {"region": "West"}, orders_path=orders,
                       events=EVENTS, root=str(tmp_path / "ws"))


def test_drills_to_new_customers_and_stops_when_even(orders, tmp_path):
    f = run(orders, tmp_path)
    assert f["final_segment"] == {"region": "West", "customer_type": "new"}
    assert f["drill_path"][0]["chosen"]["dimension"] == "customer_type"
    assert f["drill_path"][1]["chosen"] is None  # channel and category split evenly


def test_sizes_the_lead(orders, tmp_path):
    f = run(orders, tmp_path)
    assert f["lead_contribution"] == pytest.approx(1.0)  # the only thing that moved
    assert f["lead_delta"] < 0


def test_finds_the_change_point(orders, tmp_path):
    assert run(orders, tmp_path)["change_point"] == STEP.isoformat()


def test_splits_volume_from_rate(orders, tmp_path):
    vr = run(orders, tmp_path)["volume_rate"]
    assert vr["volume_share"] == pytest.approx(1.0)  # order value never changed
    assert vr["aov_base"] == vr["aov_current"] == 100.0


def test_links_the_event_that_names_the_segment(orders, tmp_path):
    ctx = run(orders, tmp_path)["linked_context"]
    assert ctx["file"] == "b.md" and ctx["days_from_change_point"] == 0


def test_keeps_scratch_tables_and_every_query(orders, tmp_path):
    f = run(orders, tmp_path)
    assert {"orders", "lead_orders", "daily_series"} <= set(f["tables"])
    assert any("CREATE TABLE lead_orders" in q for q in f["queries"])


def test_prefers_segment_mention_over_proximity():
    events = [{"date": date(2026, 8, 26), "title": "x", "file": "x.md", "text": "unrelated"},
              {"date": date(2026, 8, 28), "title": "y", "file": "y.md", "text": "email change"}]
    assert match_context(events, date(2026, 8, 26), {"channel": "email"})["file"] == "y.md"


def test_rejects_unknown_dimension(orders, tmp_path):
    with pytest.raises(ValueError):
        investigate("bad", {"region; DROP TABLE orders": "x"}, orders_path=orders,
                    events=EVENTS, root=str(tmp_path / "ws"))
