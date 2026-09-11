"""Causal Crew web app: run an investigation from the browser and read the evidence.

    make app      # then open http://localhost:8000

One investigation runs at a time: it protects the LLM quota, and each run owns
its investigators' workspaces for its duration. "Show last run" replays the
saved report without calling any LLM, which keeps a live demo safe.
"""

import json
import os
import re
import threading
import uuid
from datetime import date, timedelta

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from causal_crew import config as C
from causal_crew import question as questions
from causal_crew import run as runner
from causal_crew.segments import segment_filter

STATIC = os.path.join(os.path.dirname(__file__), "static")
DATASETS = {"clean": C.ORDERS_PATH, "broken": C.ORDERS_BROKEN_PATH}
REPORT_FILES = {"clean": "run_b_clean.json", "broken": "run_a_broken.json"}

app = FastAPI(title="Causal Crew")
app.state.run = runner.run  # swapped for a stub in tests
_runs: dict[str, dict] = {}
_busy = threading.Lock()


class RunRequest(BaseModel):
    data: str = "clean"
    question: str | None = None
    windows: dict | None = None  # {"baseline": [start, end], "current": [start, end]}


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


def _dataset(data):
    if data not in DATASETS:
        raise HTTPException(400, f"data must be one of {sorted(DATASETS)}")
    return DATASETS[data]


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


def _data_range(path):
    try:
        return [d.isoformat() for d in questions.data_range(path)]
    except Exception:  # data not generated yet
        return None


@app.get("/api/meta")
def meta():
    return {"question": C.DEMO_QUESTION, "suggestions": list(C.DEMO_SUGGESTIONS),
            "data_range": {name: _data_range(path) for name, path in DATASETS.items()},
            "window_days": {"min": C.QUESTION_MIN_WINDOW_DAYS, "max": C.QUESTION_MAX_WINDOW_DAYS,
                            "default": C.QUESTION_DEFAULT_DAYS}}


@app.post("/api/runs", status_code=202)
def start_run(req: RunRequest):
    path = _dataset(req.data)
    question = (req.question or C.DEMO_QUESTION).strip()[:300]
    if req.windows is not None:
        try:
            w = questions.parse_windows(req.windows)
            first, latest = questions.data_range(path)
            err = questions.validate_windows(w, first, latest)
        except ValueError as e:
            err = str(e)
        if err:
            raise HTTPException(400, f"Can't use those dates: {err}.")
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "An investigation is already running.")
    run_id = uuid.uuid4().hex[:12]
    state = {"id": run_id, "data": req.data, "status": "running", "events": [], "report": None, "error": None}
    _runs[run_id] = state

    def emit(stage, status, detail):
        state["events"].append({"stage": stage, "status": status, "detail": _jsonable(detail)})

    def work():
        try:
            state["report"] = _jsonable(app.state.run(path, question=question, windows=req.windows, on_event=emit))
            state["status"] = "done"
        except Exception as e:  # surface the failure to the page instead of a hung spinner
            state["error"] = f"{type(e).__name__}: {e}"
            state["status"] = "error"
        finally:
            _busy.release()

    threading.Thread(target=work, daemon=True).start()
    return {"id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in _runs:
        raise HTTPException(404, "No such run.")
    return _runs[run_id]


@app.get("/api/reports/latest")
def latest_report(data: str = "clean"):
    _dataset(data)
    path = os.path.join(runner.REPORT_DIR, REPORT_FILES[data])
    if not os.path.exists(path):
        raise HTTPException(404, "No saved run yet.")
    with open(path) as f:
        return json.load(f)


_REPORT_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _summary(r):
    top = next((f for f in (r.get("judgement") or {}).get("findings", []) if not f.get("merged_into")), None)
    return {"id": r.get("id"), "created_at": r.get("created_at"), "question": r.get("question"),
            "data": r.get("data"), "outcome": r.get("outcome"), "windows": r.get("windows"),
            "change": (r.get("headline") or {}).get("change"),
            "top": {"segment": top["segment"], "verdict": top["verdict"]} if top else None}


@app.get("/api/history")
def history(limit: int = 20):
    folder = os.path.join(runner.REPORT_DIR, "history")
    if not os.path.isdir(folder):
        return []
    items = []
    for name in sorted(os.listdir(folder), reverse=True)[: max(1, min(limit, 100))]:
        if name.endswith(".json"):
            with open(os.path.join(folder, name)) as f:
                items.append(_summary(json.load(f)))
    return items


@app.get("/api/history/{report_id}")
def history_item(report_id: str):
    if not _REPORT_ID.match(report_id):  # ids only; never a path
        raise HTTPException(400, "Bad report id.")
    path = os.path.join(runner.REPORT_DIR, "history", f"{report_id}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No such report.")
    with open(path) as f:
        return json.load(f)


@app.get("/api/series")
def series(segment: str = "{}", data: str = "clean", end: str | None = None, days: int = 70):
    """Daily revenue for one segment, ending at `end`. Values are bound as SQL parameters."""
    path = _dataset(data)
    try:
        seg = json.loads(segment)
        if not isinstance(seg, dict) or not all(isinstance(v, str) for v in seg.values()):
            raise ValueError("segment must be an object of strings")
        where, params = segment_filter(seg)
        end_date = date.fromisoformat(end) if end else date.fromisoformat(C.DEMO_CURRENT_WINDOW[1])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    end = end_date
    start = end - timedelta(days=max(14, min(days, 400)) - 1)
    with duckdb.connect() as con:
        con.read_parquet(path).create_view("orders")
        df = con.execute(f"SELECT date, sum(revenue) AS revenue, count(*) AS orders FROM orders "
                         f"WHERE {where} AND date BETWEEN ? AND ? GROUP BY date ORDER BY date",
                         params + [start, end]).df()
    return {"segment": seg,
            "points": [{"date": str(d.date()), "revenue": round(float(r), 2), "orders": int(n)}
                       for d, r, n in zip(df.date, df.revenue, df.orders, strict=True)]}
