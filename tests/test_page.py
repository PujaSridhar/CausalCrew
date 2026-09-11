"""Render real report shapes through the dashboard's own JavaScript (no browser needed)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "causal_crew" / "web" / "static" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HARNESS = """
const page = require(process.argv[2]);
const report = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
const html = page.renderResultHTML(report);
const progress = page.progressHTML(page.eventsFromReport(report));
process.stdout.write(JSON.stringify({ summary: page.summarize(report), html, progress }));
"""

CHECK = {"ok": True, "evidence": "fine"}
FINDING = {
    "rank": 1,
    "lead_id": "region-west",
    "hypothesis": "<script>alert(1)</script> may explain it",
    "start_segment": {"region": "West"},
    "segment": {"region": "West", "customer_type": "new"},
    "contribution": 0.589,
    "lead_contribution": 0.757,
    "change_point": "2026-08-27",
    "volume_rate": {
        "orders_base": 3496,
        "orders_current": 1457,
        "aov_base": 111.97,
        "aov_current": 112.45,
        "volume_share": 1.0,
    },
    "linked_context": {
        "title": "Shipping Fee Update — West Region",
        "date": "2026-08-27",
        "file": "fee.md",
        "days_from_change_point": 0,
    },
    "drill_path": [
        {
            "level": 1,
            "chosen": {"dimension": "customer_type", "value": "new"},
            "decided_by": "rocketride",
            "reason": "new customers carry 78% of the change",
        }
    ],
    "checks": {"noise": CHECK, "seasonality": CHECK, "consistency": CHECK, "timing": CHECK},
    "verdict": "SUPPORTED + CONTEXT_LINKED",
    "failed": [],
    "merged_into": None,
    "overlap": None,
}
INVESTIGATED = {
    "id": "20260911-130000-abcdef",
    "question": "Why did revenue drop?",
    "data": "orders.parquet",
    "outcome": "INVESTIGATED",
    "seconds": 17.3,
    "windows": {"baseline": ["2026-08-13", "2026-08-26"], "current": ["2026-08-27", "2026-09-09"]},
    "interpretation": {"source": "rocketride", "note": None},
    "headline": {"baseline": 2686671, "current": 2299950, "change": -0.144},
    "health": {
        "status": "PASS",
        "failed": [],
        "checks": [{"name": "freshness", "ok": True, "evidence": "ok", "days": []}],
    },
    "plan": {
        "planner": "rocketride",
        "leads": [
            {
                "lead_id": "region-west",
                "segment": {"region": "West"},
                "source": "data",
                "hypothesis": "h",
                "event_file": None,
            }
        ],
    },
    "investigations": [
        {
            "lead_id": "region-west",
            "final_segment": {"region": "West", "customer_type": "new"},
            "queries": ["SELECT 1"],
            "tables": ["orders"],
            "drill_path": FINDING["drill_path"],
        }
    ],
    "judgement": {
        "queries": ["SELECT 2"],
        "findings": [
            FINDING,
            {
                **FINDING,
                "rank": 2,
                "lead_id": "channel-email",
                "segment": {"channel": "email"},
                "verdict": "REJECTED (seasonality)",
                "failed": ["seasonality"],
                "contribution": 0.164,
                "linked_context": None,
            },
            {
                **FINDING,
                "rank": 3,
                "lead_id": "customer_type-new",
                "merged_into": "region-west",
                "overlap": 0.94,
                "verdict": "MERGED into region-west",
            },
        ],
    },
    "memory": {"prior": ["SUPPORTED: West new"], "recorded": []},
}
STOPPED = {
    "id": "20260911-130100-abcdef",
    "question": "Is last week's revenue real?",
    "data": "orders_broken.parquet",
    "outcome": "STOPPED",
    "seconds": 0.6,
    "windows": INVESTIGATED["windows"],
    "health": {
        "status": "FAIL",
        "failed": ["duplicates"],
        "checks": [
            {
                "name": "duplicates",
                "ok": False,
                "evidence": "1,433 duplicate order_ids on 2026-09-03",
                "days": ["2026-09-03"],
            }
        ],
    },
}


def _render(tmp_path, report):
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), re.S).group(1)
    (tmp_path / "page.js").write_text(script)
    (tmp_path / "harness.js").write_text(HARNESS)
    (tmp_path / "report.json").write_text(json.dumps(report))
    out = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(tmp_path / "page.js"), str(tmp_path / "report.json")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_answer_summarizes_the_judgement_in_plain_english(tmp_path):
    s = _render(tmp_path, INVESTIGATED)["summary"]
    assert s["tone"] == "found"
    assert s["title"] == "New customers in the West explain 59% of the change"
    text = " ".join(s["sentences"])
    assert "Revenue fell 14.4%, from $2.69M to $2.30M." in text
    assert "lining up with “Shipping Fee Update — West Region” (Aug 27)" in text
    assert "Email orders were ruled out: the same change happened in these weeks last year." in text
    assert "1 other lead pointed at the same orders and was merged." in text
    assert "cause" not in text.lower()


def test_stopped_run_says_dont_trust_it_and_names_the_day(tmp_path):
    out = _render(tmp_path, STOPPED)
    assert out["summary"]["title"] == "Don't trust this number yet"
    assert "on Sep 3" in out["summary"]["sentences"][0]
    assert "Duplicate orders" in out["html"]


def test_llm_text_is_escaped(tmp_path):
    html = _render(tmp_path, INVESTIGATED)["html"]
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_progress_shows_every_stage_and_the_crew(tmp_path):
    progress = _render(tmp_path, INVESTIGATED)["progress"]
    for stage in ("Question", "Data health", "Planner", "Investigators", "Judge", "Memory", "Report"):
        assert stage in progress
    assert "Orders in the West" in progress
