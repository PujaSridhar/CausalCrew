from pathlib import Path

import pytest

pytest.importorskip("rocketride")  # CI installs only the data stack

from causal_crew.rocketride_llm import INVESTIGATOR_PIPE, PLANNER_PIPE, _pipeline


def test_each_run_gets_its_own_project_id():
    a, b = _pipeline(INVESTIGATOR_PIPE, "inv-west"), _pipeline(INVESTIGATOR_PIPE, "inv-west")
    assert a["project_id"] != b["project_id"] and a["project_id"].startswith("inv-west-")
    assert a["components"] == b["components"]


@pytest.mark.parametrize("pipe", [PLANNER_PIPE, INVESTIGATOR_PIPE])
def test_pipes_hold_only_a_key_placeholder(pipe):
    text = Path(pipe).read_text()
    assert "${ROCKETRIDE_GEMINI_KEY}" in text and "AIza" not in text
