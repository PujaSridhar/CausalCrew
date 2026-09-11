import os
from datetime import date
from pathlib import Path

import pandas as pd

from causal_crew.investigator import rule_decider
from causal_crew.run import run

NO_LLM = lambda _prompt: '{"leads": []}'  # noqa: E731


def test_clean_run_investigates_and_writes_report(orders, tmp_path):
    mem = str(tmp_path / "memory.jsonl")
    opts = {"out_dir": str(tmp_path / "reports"), "ask": NO_LLM, "root": str(tmp_path / "ws"),
            "memory_path": mem, "decide": rule_decider}
    r = run(orders, **opts)
    assert r["outcome"] == "INVESTIGATED"
    assert len(r["memory"]["recorded"]) == 1 and r["memory"]["prior"] == []
    again = run(orders, **opts)
    assert again["memory"]["recorded"] == [] and len(again["memory"]["prior"]) == 1
    top = r["judgement"]["findings"][0]
    assert top["verdict"].startswith("SUPPORTED")
    md = Path(r["files"]["markdown"]).read_text()
    assert "SQL behind every number" in md and "does not prove causation" in md
    assert os.path.exists(r["files"]["json"])


def test_broken_run_stops_at_health_check(orders, tmp_path):
    df = pd.read_parquet(orders)
    broken = tmp_path / "broken.parquet"
    pd.concat([df, df[df.date == date(2026, 9, 3)]]).to_parquet(broken)
    r = run(str(broken), out_dir=str(tmp_path / "reports"), ask=NO_LLM, root=str(tmp_path / "ws"),
            memory_path=str(tmp_path / "memory.jsonl"), decide=rule_decider)
    assert r["outcome"] == "STOPPED" and "plan" not in r
    assert "Don't trust this number yet" in Path(r["files"]["markdown"]).read_text()
