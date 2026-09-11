import os
from datetime import date

import pandas as pd

from causal_crew.run import run

NO_LLM = lambda _prompt: '{"leads": []}'  # noqa: E731


def test_clean_run_investigates_and_writes_report(orders, tmp_path):
    r = run(orders, out_dir=str(tmp_path / "reports"), ask=NO_LLM, root=str(tmp_path / "ws"))
    assert r["outcome"] == "INVESTIGATED"
    top = r["judgement"]["findings"][0]
    assert top["verdict"].startswith("SUPPORTED")
    md = open(r["files"]["markdown"]).read()
    assert "SQL behind every number" in md and "does not prove causation" in md
    assert os.path.exists(r["files"]["json"])


def test_broken_run_stops_at_health_check(orders, tmp_path):
    df = pd.read_parquet(orders)
    broken = tmp_path / "broken.parquet"
    pd.concat([df, df[df.date == date(2026, 9, 3)]]).to_parquet(broken)
    r = run(str(broken), out_dir=str(tmp_path / "reports"), ask=NO_LLM, root=str(tmp_path / "ws"))
    assert r["outcome"] == "STOPPED" and "plan" not in r
    assert "Don't trust this number yet" in open(r["files"]["markdown"]).read()
