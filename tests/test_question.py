from datetime import date, timedelta

import pytest

from causal_crew.question import interpret, parse_windows, rule_windows, validate_windows

LATEST = date(2026, 9, 9)


@pytest.mark.parametrize("q,days", [
    ("Revenue dropped in the last two weeks. Why?", 14),
    ("What happened over the past 3 weeks?", 21),
    ("Why did revenue move over the past month?", 30),
    ("Is last week's revenue number real?", 7),
    ("Why is revenue down?", 14),  # no period named: the default
])
def test_rule_reads_the_period(q, days):
    (b0, b1), (c0, c1) = rule_windows(q, LATEST).values()
    assert c1 == LATEST and (c1 - c0).days + 1 == days
    assert b1 == c0 - timedelta(days=1) and (b1 - b0).days + 1 == days


def test_default_question_gives_the_demo_windows():
    w = rule_windows("Revenue dropped in the last two weeks. Why?", LATEST)
    assert w == {"baseline": (date(2026, 8, 13), date(2026, 8, 26)), "current": (date(2026, 8, 27), LATEST)}


def test_validate_rejects_bad_windows():
    first = date(2025, 8, 1)
    ok = {"baseline": (date(2026, 8, 13), date(2026, 8, 26)), "current": (date(2026, 8, 27), LATEST)}
    assert validate_windows(ok, first, LATEST) is None
    assert "before" in validate_windows({**ok, "baseline": (date(2026, 8, 28), date(2026, 9, 1))}, first, LATEST)
    assert "covers" in validate_windows({**ok, "current": (date(2026, 8, 27), date(2026, 9, 20))}, first, LATEST)
    assert "days" in validate_windows({**ok, "current": (date(2026, 9, 8), LATEST)}, first, LATEST)


def test_parse_windows_rejects_malformed_input():
    with pytest.raises(ValueError):
        parse_windows({"current": ["2026-08-27"]})
    with pytest.raises(ValueError):
        parse_windows({"baseline": ["x", "y"], "current": ["2026-08-27", "2026-09-09"]})


def test_relative_periods_never_ask_the_llm(orders):
    def must_not_call(_):
        raise AssertionError("LLM called for date arithmetic")
    r = interpret("Revenue dropped in the last two weeks. Why?", orders, ask=must_not_call)
    assert r["source"] == "rule"
    assert r["windows"] == {"baseline": (date(2026, 8, 13), date(2026, 8, 26)), "current": (date(2026, 8, 27), LATEST)}


def test_llm_windows_are_used_when_valid(orders):
    reply = '{"current": ["2026-08-20", "2026-09-09"], "baseline": ["2026-07-30", "2026-08-19"]}'
    r = interpret("What changed since the fee update?", orders, ask=lambda _: reply)
    assert r["source"] == "llm" and r["windows"]["current"] == (date(2026, 8, 20), LATEST)


def test_invalid_llm_windows_fall_back_to_the_rule(orders):
    reply = '{"current": ["2026-09-01", "2026-12-31"], "baseline": ["2026-08-01", "2026-08-31"]}'
    r = interpret("Why is revenue down?", orders, ask=lambda _: reply)
    assert r["source"] == "rule" and r["windows"]["current"] == (date(2026, 8, 27), LATEST)
    assert "LLM" in r["note"]


def test_explicit_windows_are_validated(orders):
    good = {"baseline": ["2026-08-13", "2026-08-26"], "current": ["2026-08-27", "2026-09-09"]}
    assert interpret("x", orders, windows=good)["source"] == "user"
    with pytest.raises(ValueError):
        backwards = {"baseline": ["2026-09-01", "2026-09-05"], "current": ["2026-08-01", "2026-08-05"]}
        interpret("x", orders, windows=backwards)
