from causal_crew import memory

WINDOWS = {"baseline": ["2026-08-13", "2026-08-26"], "current": ["2026-08-27", "2026-09-09"]}


def _finding(verdict, seg, cp="2026-08-27"):
    return {"verdict": verdict, "segment": seg, "contribution": 0.59, "change_point": cp,
            "linked_context": {"title": "Fee", "date": "2026-08-27", "file": "fee.md", "extra": "x"}}


def test_only_supported_findings_are_remembered(tmp_path):
    path = str(tmp_path / "m.jsonl")
    new = memory.write_back([_finding("SUPPORTED + CONTEXT_LINKED", {"region": "West"}),
                             _finding("REJECTED (seasonality)", {"channel": "email"}),
                             _finding("MERGED into region-west", {"customer_type": "new"})],
                            "why?", WINDOWS, path)
    assert [r["segment"] for r in new] == [{"region": "West"}]
    assert memory.recall(path) == new


def test_same_finding_is_not_recorded_twice(tmp_path):
    path = str(tmp_path / "m.jsonl")
    f = [_finding("SUPPORTED", {"region": "West"})]
    assert len(memory.write_back(f, "why?", WINDOWS, path)) == 1
    assert memory.write_back(f, "why?", WINDOWS, path) == []
    assert len(memory.recall(path)) == 1


def test_describe_uses_attribution_language():
    text = memory.describe({**_finding("SUPPORTED", {"region": "West"}), "windows": WINDOWS})
    assert "accounts for 59%" in text and "cause" not in text.lower()
