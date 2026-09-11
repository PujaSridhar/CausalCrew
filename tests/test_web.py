import threading
import time

import pytest

pytest.importorskip("fastapi")  # CI installs only the data stack
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from causal_crew import run as runner
from causal_crew.web import app as web

client = TestClient(web.app)


def _wait(run_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/runs/{run_id}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_page_and_meta_are_served():
    assert "Causal Crew" in client.get("/").text
    assert "question" in client.get("/api/meta").json()


def test_run_streams_progress_and_returns_report(monkeypatch):
    def fake_run(path, on_event, **_):
        on_event("health", "pass", {"failed": []})
        on_event("run", "done", {})
        return {"outcome": "INVESTIGATED", "data": path}
    monkeypatch.setattr(web.app.state, "run", fake_run)
    state = _wait(client.post("/api/runs", json={"data": "clean"}).json()["id"])
    assert state["status"] == "done" and state["report"]["outcome"] == "INVESTIGATED"
    assert [e["stage"] for e in state["events"]] == ["health", "run"]


def test_only_one_investigation_at_a_time(monkeypatch):
    release = threading.Event()

    def slow_run(path, on_event, **_):
        release.wait(5)
        return {"outcome": "INVESTIGATED"}
    monkeypatch.setattr(web.app.state, "run", slow_run)
    first = client.post("/api/runs", json={"data": "clean"}).json()["id"]
    assert client.post("/api/runs", json={"data": "clean"}).status_code == 409
    release.set()
    assert _wait(first)["status"] == "done"


def test_failed_run_reports_the_error(monkeypatch):
    def broken_run(path, on_event, **_):
        raise RuntimeError("boom")
    monkeypatch.setattr(web.app.state, "run", broken_run)
    state = _wait(client.post("/api/runs", json={"data": "clean"}).json()["id"])
    assert state["status"] == "error" and "boom" in state["error"]


def test_rejects_unknown_dataset_and_bad_segments():
    assert client.post("/api/runs", json={"data": "prod"}).status_code == 400
    assert client.get("/api/series", params={"segment": '{"planet": "Mars"}'}).status_code == 400
    assert client.get("/api/series", params={"segment": "not json"}).status_code == 400


def test_latest_report_404_until_a_run_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "REPORT_DIR", str(tmp_path))
    assert client.get("/api/reports/latest", params={"data": "clean"}).status_code == 404


def test_bad_dates_are_rejected_before_running(monkeypatch):
    monkeypatch.setattr(web.app.state, "run", lambda *a, **k: {"outcome": "INVESTIGATED"})
    bad = {"baseline": ["2026-09-01", "2026-09-05"], "current": ["2026-08-01", "2026-08-05"]}
    r = client.post("/api/runs", json={"data": "clean", "windows": bad})
    assert r.status_code in (400, 404) or "dates" in r.text


def test_question_and_windows_reach_the_pipeline(monkeypatch):
    seen = {}

    def fake_run(path, on_event, question, windows):
        seen.update(question=question, windows=windows)
        return {"outcome": "INVESTIGATED"}
    monkeypatch.setattr(web.app.state, "run", fake_run)
    _wait(client.post("/api/runs", json={"data": "clean", "question": "  Why is revenue down?  "}).json()["id"])
    assert seen == {"question": "Why is revenue down?", "windows": None}


def test_history_lists_saved_runs_and_rejects_path_ids(monkeypatch, tmp_path):
    import json
    (tmp_path / "history").mkdir()
    report = {"id": "20260911-130000-abcdef", "created_at": "2026-09-11T20:00:00+00:00",
              "question": "q", "data": "orders.parquet", "outcome": "STOPPED", "windows": {}}
    (tmp_path / "history" / "20260911-130000-abcdef.json").write_text(json.dumps(report))
    monkeypatch.setattr(runner, "REPORT_DIR", str(tmp_path))
    items = client.get("/api/history").json()
    assert [i["id"] for i in items] == ["20260911-130000-abcdef"]
    assert client.get("/api/history/20260911-130000-abcdef").json()["question"] == "q"
    assert client.get("/api/history/..%2F..%2Fetc").status_code in (400, 404)
