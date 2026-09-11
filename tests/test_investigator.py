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


# --- LLM path decisions (stubbed; no network) --------------------------------

from causal_crew.investigator import llm_decider  # noqa: E402

CANDS = [{"dimension": "customer_type", "top_value": "new", "delta_share": 0.78, "base_share": 0.46,
          "concentration": 1.69},
         {"dimension": "channel", "top_value": "web", "delta_share": 0.51, "base_share": 0.52,
          "concentration": 0.99}]


def _stub(reply):
    def ask(_prompt):
        return reply
    ask.engine = "rocketride"
    return ask


def test_llm_choice_among_qualifying_is_used():
    d = llm_decider("w", "h", {"region": "West"}, 1, CANDS,
                    ask=_stub('{"dimension": "customer_type", "reason": "new customers carry 78% of the change"}'))
    assert (d["dimension"], d["value"], d["decided_by"]) == ("customer_type", "new", "rocketride")
    assert "78%" in d["reason"]


def test_unsupported_llm_choice_is_overridden_by_rule():
    d = llm_decider("w", "h", {"region": "West"}, 1, CANDS, ask=_stub('{"dimension": "channel", "reason": "x"}'))
    assert d["dimension"] == "customer_type" and d["decided_by"] == "rule (overrode rocketride)"


def test_llm_outage_falls_back_to_rule():
    def down(_):
        raise TimeoutError
    d = llm_decider("w", "h", {"region": "West"}, 1, CANDS, ask=down)
    assert d["dimension"] == "customer_type" and "LLM unavailable" in d["reason"]


def test_rationale_with_invented_number_is_withheld():
    d = llm_decider("w", "h", {"region": "West"}, 1, CANDS,
                    ask=_stub('{"dimension": "customer_type", "reason": "a 93% collapse"}'))
    assert d["dimension"] == "customer_type" and "withheld" in d["reason"]


def test_deeper_level_with_nothing_to_choose_skips_the_llm():
    def must_not_call(_):
        raise AssertionError("LLM called")
    flat = [dict(CANDS[1])]
    d = llm_decider("w", "h", {"region": "West", "customer_type": "new"}, 2, flat, ask=must_not_call)
    assert d["dimension"] is None and d["decided_by"] == "rule"
