"""Run LLM prompts through the RocketRide pipeline in pipelines/planner.pipe.

The .pipe holds only a ${ROCKETRIDE_GEMINI_KEY} placeholder; the real key is
passed at run time from LLM_API_KEY in .env, so it never lands in the repo.
The Gemini node's config layout follows rocketride-server's
examples/llm-benchmark.pipe.
"""

import asyncio
import os

from dotenv import load_dotenv
from rocketride import RocketRideClient
from rocketride.schema.question import Question

from causal_crew import config as C

PIPE_PATH = os.path.join(os.path.dirname(C.CONTEXT_DIR), "pipelines", "planner.pipe")


async def _ask(prompt, timeout):
    load_dotenv(C.ENV_PATH)
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY is not set")
    async with RocketRideClient() as client:  # reads ROCKETRIDE_URI and ROCKETRIDE_APIKEY
        run = await asyncio.wait_for(
            client.use(filepath=PIPE_PATH, name="causal-crew-planner",
                       env={"ROCKETRIDE_GEMINI_KEY": key}), timeout)
        try:
            question = Question()
            question.addQuestion(prompt)
            out = await asyncio.wait_for(client.chat(token=run["token"], question=question), timeout)
        finally:
            await client.terminate(run["token"])
    answers = out.get("answers") or []
    if not answers:
        raise RuntimeError(f"RocketRide returned no answers: {list(out.keys())}")
    return answers[0]


def ask_rocketride(prompt, timeout=C.GEMINI_TIMEOUT_S):
    return asyncio.run(_ask(prompt, timeout))
