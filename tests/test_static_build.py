"""The public demo must be self-contained: no API calls, no keys, charts included."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("fastapi")
if not (ROOT / "data" / "orders.parquet").exists():
    pytest.skip("demo data not generated", allow_module_level=True)

spec = importlib.util.spec_from_file_location("build_static", ROOT / "scripts" / "build_static.py")
build_static = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_static)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("public")
    build_static.build(out_dir=str(out), limit=4)
    return out


def test_page_is_marked_as_the_static_demo(site):
    page = (site / "index.html").read_text()
    assert "window.CAUSAL_CREW_STATIC = true" in page
    assert "Causal Crew" in page


def test_metadata_and_history_are_prebuilt(site):
    assert json.loads((site / "data" / "meta.json").read_text())["question"]
    history = json.loads((site / "data" / "history.json").read_text())
    assert history and all(item["id"] for item in history)
    for item in history:
        assert (site / "data" / "reports" / f"{item['id']}.json").exists()


def test_every_chart_has_its_data_baked_in(site):
    investigated = 0
    for name in os.listdir(site / "data" / "reports"):
        report = json.loads((site / "data" / "reports" / name).read_text())
        if report["outcome"] != "INVESTIGATED":
            continue
        investigated += 1
        for finding in report["judgement"]["findings"]:
            if finding.get("merged_into"):
                continue
            points = report["series"][finding["lead_id"]]
            assert len(points) > 30 and points[0]["date"] < points[-1]["date"]
    assert investigated, "expected at least one full investigation in the demo"


def test_no_key_material_ships(site):
    from dotenv import dotenv_values
    secrets = [v.strip() for v in dotenv_values(ROOT / ".env").values() if v and len(v.strip()) > 8]
    for dirpath, _, names in os.walk(site):
        for name in names:
            text = Path(dirpath, name).read_text(errors="ignore")
            assert not any(s in text for s in secrets)
