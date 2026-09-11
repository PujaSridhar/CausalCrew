"""Question: turn what the user asked into two date windows to compare.

Relative periods ("last two weeks", "past month") are date arithmetic, so they
are computed deterministically: an LLM asked for "the last two weeks" once
returned 15-day windows. Anything else ("since the fee change", "in August") is
read by the LLM (Gemini via RocketRide), and its proposal is validated against
the data before anything runs; if it is missing or invalid, the latest 14 days
are compared with the 14 before. Revenue is the only metric in this version.
"""

import re
from datetime import date, timedelta

import duckdb

from causal_crew import config as C
from causal_crew.llm import parse_json, planner_llm

_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_PHRASE = re.compile(r"\b(?:last|past|previous|prior)\s+(?:(\d+|a|one|two|three|four|five|six|eight|ten)\s+)?"
                     r"(day|week|month)s?\b")


def data_range(orders_path):
    with duckdb.connect() as con:
        return con.execute("SELECT min(date), max(date) FROM read_parquet(?)", [orders_path]).fetchone()


def _span(days, latest):
    c0 = latest - timedelta(days=days - 1)
    b1 = c0 - timedelta(days=1)
    return {"baseline": (b1 - timedelta(days=days - 1), b1), "current": (c0, latest)}


def rule_windows(question, latest):
    """'last N days/weeks/months' -> that span vs the one before it; otherwise the default span."""
    days = C.QUESTION_DEFAULT_DAYS
    m = _PHRASE.search(question.lower())
    if m:
        n = m.group(1)
        days = (int(n) if n and n.isdigit() else _NUMBERS.get(n, 1)) * _UNIT_DAYS[m.group(2)]
    return _span(max(C.QUESTION_MIN_WINDOW_DAYS, min(days, C.QUESTION_MAX_WINDOW_DAYS)), latest)


def parse_windows(raw):
    """{'baseline': [start, end], 'current': [start, end]} as ISO strings or dates -> date tuples."""
    try:
        w = {k: tuple(date.fromisoformat(str(d)) for d in raw[k]) for k in ("baseline", "current")}
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"windows need baseline and current as [start, end] dates ({e})") from e
    if any(len(v) != 2 for v in w.values()):
        raise ValueError("each window needs exactly a start and an end date")
    return w


def validate_windows(w, first, latest):
    """Return why the windows can't be used, or None."""
    (b0, b1), (c0, c1) = w["baseline"], w["current"]
    if not b0 <= b1 < c0 <= c1:
        return "the baseline must end before the current window starts"
    if b0 < first or c1 > latest:
        return f"the data covers {first} to {latest}"
    for name, (lo, hi) in w.items():
        if not C.QUESTION_MIN_WINDOW_DAYS <= (hi - lo).days + 1 <= C.QUESTION_MAX_WINDOW_DAYS:
            return f"the {name} window must be {C.QUESTION_MIN_WINDOW_DAYS} to {C.QUESTION_MAX_WINDOW_DAYS} days"
    return None


def _prompt(question, first, latest):
    return f"""Turn this business question into two date windows to compare.

Question: {question}
The orders data covers {first} to {latest}. Treat {latest} as today.

Rules:
- "current" is the period the question asks about; "baseline" is what it is compared with.
- If the question names no period, use the last 14 days vs the 14 days before.
- If it names a period but no comparison, the baseline is the equally long period right before.
- Both windows must lie inside the data, and the baseline must end before the current window starts.

Return JSON only: {{"current": ["YYYY-MM-DD", "YYYY-MM-DD"], "baseline": ["YYYY-MM-DD", "YYYY-MM-DD"]}}"""


def interpret(question, orders_path, ask=None, windows=None):
    """Windows to compare: the user's own (validated), else the LLM's (validated), else the rule's."""
    first, latest = data_range(orders_path)
    base = {"data_range": [first.isoformat(), latest.isoformat()]}
    if windows is not None:
        w = parse_windows(windows)
        err = validate_windows(w, first, latest)
        if err:
            raise ValueError(f"Can't use those dates: {err}.")
        return {**base, "windows": w, "source": "user", "note": None}
    if _PHRASE.search(question.lower()):  # relative period: arithmetic, not a job for the LLM
        return {**base, "windows": rule_windows(question, latest), "source": "rule", "note": None}
    ask = ask or planner_llm()
    try:
        w = parse_windows(parse_json(ask(_prompt(question, first, latest))))
        err = validate_windows(w, first, latest)
        if err:
            raise ValueError(err)
        return {**base, "windows": w, "source": getattr(ask, "engine", None) or "llm", "note": None}
    except Exception as e:  # never let a bad reply decide the dates; fall back to the rule
        return {**base, "windows": rule_windows(question, latest), "source": "rule",
                "note": f"dates read from the question's wording ({type(e).__name__} from the LLM)"}
