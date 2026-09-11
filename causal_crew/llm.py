"""LLM plumbing shared by the agents: RocketRide first, Gemini direct as backup.

Agents never take a number or a verdict from an LLM. They use it to choose
among options computed in SQL and to write hypotheses, and they validate
every reply before using it.
"""

import ast
import json
import os
import re
import urllib.request

from dotenv import load_dotenv

from causal_crew import config as C

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def ask_gemini(prompt, model):
    """Call Gemini directly. The key goes in a header, never the URL."""
    load_dotenv(C.ENV_PATH)
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY is not set")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    req = urllib.request.Request(GEMINI_URL.format(model=model.split("/", 1)[-1]),
                                 data=json.dumps(body).encode(),
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=C.GEMINI_TIMEOUT_S) as r:
        data = json.load(r)
    return data["candidates"][0]["content"]["parts"][0]["text"]


class LLMChain:
    """Try each engine in order; remember which one answered and why others failed."""

    def __init__(self, *engines):
        self.engines, self.engine, self.errors = engines, None, []

    def __call__(self, prompt):
        self.errors = []
        for name, fn in self.engines:
            try:
                text = fn(prompt)
                self.engine = name
                return text
            except Exception as e:
                self.errors.append(f"{name}: {type(e).__name__}: {str(e)[:150]}")
        raise RuntimeError("; ".join(self.errors))


def parse_json(text):
    """Pull the object out of a reply, tolerating ```json fences and Python-style dicts.

    RocketRide's Gemini node can hand back a Python repr (single quotes) instead of
    JSON. ast.literal_eval accepts literals only and never executes code; callers
    still validate every field.
    """
    m = re.search(r"\{.*\}", text.strip(), re.S)
    body = m.group(0) if m else text
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        value = ast.literal_eval(body)
        if not isinstance(value, dict):
            raise ValueError("the reply is not an object") from None
        return value


def planner_llm():
    from causal_crew.rocketride_llm import PLANNER_PIPE, ask_rocketride
    return LLMChain(
        ("rocketride", lambda p: ask_rocketride(p, PLANNER_PIPE, "causal-crew-planner")),
        ("gemini", lambda p: ask_gemini(p, os.environ.get("PLANNER_MODEL") or C.PLANNER_MODEL)))


def investigator_llm(lead_id):
    """Each investigator gets its own chain, so its RocketRide run is its own task."""
    from causal_crew.rocketride_llm import INVESTIGATOR_PIPE, ask_rocketride
    return LLMChain(
        ("rocketride", lambda p: ask_rocketride(p, INVESTIGATOR_PIPE, f"causal-crew-investigator-{lead_id}")),
        ("gemini", lambda p: ask_gemini(p, C.INVESTIGATOR_MODEL)))
