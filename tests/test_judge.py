from datetime import date

import numpy as np

from causal_crew.investigator import investigate
from causal_crew.judge import bootstrap_ci, judge, verdict

EVENTS = [{"date": date(2026, 8, 27), "title": "West shipping fee", "file": "fee.md",
           "text": "Shipping fee for West orders rises."}]


def _find(orders, tmp_path, lead_id, seg):
    return investigate(lead_id, seg, orders_path=orders, events=EVENTS, root=str(tmp_path / "ws"))


# --- verdict rules (pure) ---------------------------------------------------

def test_supported_and_linked():
    assert verdict(True, True, True, True, 0.6) == ("SUPPORTED + CONTEXT_LINKED", [])


def test_supported_without_timing():
    assert verdict(True, True, True, False, 0.6) == ("SUPPORTED", [])


def test_small_contribution_is_insufficient():
    assert verdict(True, True, True, True, 0.1) == ("INSUFFICIENT_EVIDENCE", [])


def test_rejected_names_every_failed_check():
    assert verdict(False, True, False, True, 0.9) == ("REJECTED (noise, consistency)",
                                                       ["noise", "consistency"])


def test_bootstrap_separates_drop_from_noise():
    lo, hi = bootstrap_ci([90] * 14, [100] * 14)
    assert hi < 0
    rng = np.random.default_rng(0)
    lo, hi = bootstrap_ci(rng.normal(100, 20, 14), rng.normal(100, 20, 14))
    assert lo < 0 < hi


# --- end to end on deterministic data ----------------------------------------

def test_planted_cause_is_supported_and_linked(orders, tmp_path):
    out = judge([_find(orders, tmp_path, "region-west", {"region": "West"})], orders_path=orders)
    f = out["findings"][0]
    assert f["verdict"] == "SUPPORTED + CONTEXT_LINKED", f["checks"]
    assert f["segment"] == {"region": "West", "customer_type": "new"}


def test_seasonal_decoy_is_rejected(orders_decoy, tmp_path):
    f = _find(orders_decoy, tmp_path, "east-app", {"region": "East", "channel": "app"})
    out = judge([f], orders_path=orders_decoy)["findings"][0]
    assert out["verdict"] == "REJECTED (seasonality)", out["checks"]["seasonality"]


def test_overlapping_lead_is_merged(orders, tmp_path):
    west = _find(orders, tmp_path, "region-west", {"region": "West"})
    new = _find(orders, tmp_path, "customer_type-new", {"customer_type": "new"})
    out = judge([west, new], orders_path=orders)["findings"]
    assert out[0]["lead_id"] == "region-west" and out[0]["merged_into"] is None
    assert out[1]["merged_into"] == "region-west" and out[1]["verdict"].startswith("MERGED")


def test_every_judge_number_has_its_sql(orders, tmp_path):
    out = judge([_find(orders, tmp_path, "region-west", {"region": "West"})], orders_path=orders)
    assert out["queries"] and all(q.startswith("SELECT") for q in out["queries"])
