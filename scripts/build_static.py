"""Build the public, read-only demo site into public/.

Everything is prebuilt: the saved investigations, the daily series each chart
draws, and the metadata the page loads at startup. The result is plain files,
so it can be served by any static host. No server, no keys, no LLM calls.

    .venv/bin/python scripts/build_static.py
"""

import json
import os
import shutil
import sys
from datetime import date, timedelta

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from causal_crew import config as C  # noqa: E402
from causal_crew import run as runner  # noqa: E402
from causal_crew.segments import segment_filter  # noqa: E402
from causal_crew.web import app as web  # noqa: E402

PUBLIC = os.path.join(ROOT, "public")
PAGE = os.path.join(ROOT, "causal_crew", "web", "static", "index.html")
FLAG = "<script>window.CAUSAL_CREW_STATIC = true;</script>\n"
SERIES_DAYS = 70


def _dataset(report):
    return "broken" if "broken" in str(report.get("data", "")) else "clean"


def series(orders_path, segment, end_iso, days=SERIES_DAYS):
    """Daily revenue for one segment, the same query the live app runs."""
    where, params = segment_filter(segment)
    end = date.fromisoformat(end_iso)
    start = end - timedelta(days=days - 1)
    with duckdb.connect() as con:
        con.read_parquet(orders_path).create_view("orders")
        df = con.execute(f"SELECT date, sum(revenue) AS revenue, count(*) AS orders FROM orders "
                         f"WHERE {where} AND date BETWEEN ? AND ? GROUP BY date ORDER BY date",
                         params + [start, end]).df()
    return [{"date": str(d.date()), "revenue": round(float(r), 2), "orders": int(n)}
            for d, r, n in zip(df.date, df.revenue, df.orders, strict=True)]


def load_reports(limit=12):
    folder = os.path.join(runner.REPORT_DIR, "history")
    if not os.path.isdir(folder):
        return []
    reports = []
    for name in sorted(os.listdir(folder), reverse=True)[:limit]:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name)) as f:
            report = json.load(f)
        if report.get("id") and report.get("outcome"):
            reports.append(report)
    return reports


def attach_series(report):
    """Bake each finding's chart data into the report, so the page needs no API."""
    if report["outcome"] != "INVESTIGATED":
        return report
    path = web.DATASETS[_dataset(report)]
    end = report["windows"]["current"][1]
    report["series"] = {f["lead_id"]: series(path, f["segment"], end)
                        for f in report["judgement"]["findings"] if not f.get("merged_into")}
    return report


def _no_secrets(text):
    """Never ship a key, even by accident."""
    from dotenv import dotenv_values
    for name, value in dotenv_values(C.ENV_PATH).items():
        value = (value or "").strip()
        if len(value) > 8 and value in text:
            raise SystemExit(f"refusing to build: {name} appears in the output")


def build(out_dir=PUBLIC, limit=12):
    reports = [attach_series(r) for r in load_reports(limit)]
    if not reports:
        raise SystemExit("no saved investigations in reports/history; run `make demo-b` first")

    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(os.path.join(out_dir, "data", "reports"), exist_ok=True)

    with open(PAGE) as f:
        page = f.read()
    page = page.replace("</head>", FLAG + "</head>", 1)
    payload = {"index.html": page,
               os.path.join("data", "meta.json"): json.dumps(web.meta(), indent=2),
               os.path.join("data", "history.json"): json.dumps([web._summary(r) for r in reports], indent=2)}
    for report in reports:
        payload[os.path.join("data", "reports", f"{report['id']}.json")] = json.dumps(report, indent=2, default=str)

    _no_secrets("\n".join(payload.values()))
    for name, text in payload.items():
        with open(os.path.join(out_dir, name), "w") as f:
            f.write(text)
    return {"reports": len(reports), "files": len(payload), "out_dir": out_dir}


if __name__ == "__main__":
    result = build()
    total = sum(os.path.getsize(os.path.join(dirpath, name))
                for dirpath, _, names in os.walk(result["out_dir"]) for name in names)
    print(f"built {result['files']} files ({total / 1024:.0f} KB) from "
          f"{result['reports']} saved investigations -> {result['out_dir']}")
