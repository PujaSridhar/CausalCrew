import json
from datetime import date

from causal_crew.planner import plan, validate

EVENTS = [
    {"date": date(2026, 8, 25), "title": "Email campaign ends", "file": "email.md",
     "text": "The summer email campaign ended in every region."},
    {"date": date(2026, 8, 27), "title": "West shipping fee", "file": "fee.md",
     "text": "Shipping fee for West orders rises."},
    {"date": date(2025, 1, 1), "title": "Old note", "file": "old.md",
     "text": "Something about app checkout, long ago."},
]
VALUES = {"region": ["East", "West"], "channel": ["app", "web"]}


def test_data_driven_leads_always_come_first(orders):
    p = plan(orders_path=orders, events=EVENTS, ask=lambda _: '{"leads": []}')
    segs = [lead["segment"] for lead in p["leads"] if lead["source"] == "data"]
    assert {"region": "West"} in segs and {"customer_type": "new"} in segs
    assert p["leads"][0]["source"] == "data"


def test_uses_gemini_leads_when_valid(orders):
    reply = json.dumps({"leads": [{"segment": {"channel": "web"}, "hypothesis": "h",
                                   "event_file": "email.md"}]})
    p = plan(orders_path=orders, events=EVENTS, ask=lambda _: reply)
    assert p["planner"] == "llm"  # a stub with no .engine is reported generically
    assert {"channel": "web"} in [lead["segment"] for lead in p["leads"]]


def test_falls_back_when_gemini_fails(orders):
    def boom(_):
        raise TimeoutError("gemini down")
    p = plan(orders_path=orders, events=EVENTS, ask=boom)
    assert p["planner"] == "fallback" and "TimeoutError" in p["error"]
    assert len(p["leads"]) >= 2  # data-driven leads survive


def test_only_considers_context_near_the_window(orders):
    p = plan(orders_path=orders, events=EVENTS, ask=lambda _: '{"leads": []}')
    assert "old.md" not in p["context_considered"]


def test_never_exceeds_four_leads(orders):
    many = json.dumps({"leads": [{"segment": {"channel": v}, "hypothesis": "h"}
                                 for v in ("web", "app")] +
                                [{"segment": {"product_category": v}, "hypothesis": "h"}
                                 for v in ("a", "b")]})
    assert len(plan(orders_path=orders, events=EVENTS, ask=lambda _: many)["leads"]) <= 4


def test_validate_drops_bad_and_duplicate_leads():
    cands = [{"segment": {"region": "Mars"}},            # unknown value
             {"segment": {"planet": "West"}},            # unknown dimension
             {"segment": {"region": "West"}},            # already taken
             {"segment": {"channel": "app"}, "event_file": "nope.md"},
             {"segment": {"channel": "app"}}]            # duplicate of the one above
    out = validate(cands, VALUES, taken=[{"region": "West"}], event_files={"email.md"})
    assert [lead["segment"] for lead in out] == [{"channel": "app"}]
    assert out[0]["event_file"] is None


def test_chain_falls_through_to_next_engine():
    from causal_crew.llm import LLMChain

    def down(_):
        raise TimeoutError("rocketride down")
    chain = LLMChain(("rocketride", down), ("gemini", lambda _: '{"leads": []}'))
    assert chain("p") == '{"leads": []}' and chain.engine == "gemini"
    assert "rocketride: TimeoutError" in chain.errors[0]


def test_parse_json_tolerates_code_fences():
    from causal_crew.llm import parse_json
    assert parse_json('```json\n{"leads": []}\n```') == {"leads": []}
