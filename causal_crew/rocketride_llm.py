"""Run LLM prompts as RocketRide pipelines.

Every call starts its own pipeline run (use -> chat -> terminate) under a
unique project id: RocketRide allows one running task per project, so agents
calling in parallel would otherwise collide ("Pipeline is already running"). The .pipe files
hold only a ${ROCKETRIDE_GEMINI_KEY} placeholder; the real key is passed at
run time from LLM_API_KEY in .env, so it never lands in the repo. The Gemini
node's config layout follows rocketride-server's examples/llm-benchmark.pipe.
"""

import asyncio
import json
import os
import uuid

from dotenv import load_dotenv
from rocketride import RocketRideClient
from rocketride.schema.question import Question

from causal_crew import config as C

PIPELINE_DIR = os.path.join(os.path.dirname(C.CONTEXT_DIR), "pipelines")
PLANNER_PIPE = os.path.join(PIPELINE_DIR, "planner.pipe")
INVESTIGATOR_PIPE = os.path.join(PIPELINE_DIR, "investigator.pipe")


def _pipeline(pipe_path, name):
    """Load a .pipe and give this run its own project id."""
    with open(pipe_path) as f:
        pipeline = json.load(f)
    pipeline["project_id"] = f"{name}-{uuid.uuid4().hex[:8]}"
    return pipeline


async def _ask(prompt, pipe_path, name, timeout):
    load_dotenv(C.ENV_PATH)
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY is not set")
    async with RocketRideClient() as client:  # reads ROCKETRIDE_URI and ROCKETRIDE_APIKEY
        run = await asyncio.wait_for(
            client.use(pipeline=_pipeline(pipe_path, name), name=name, env={"ROCKETRIDE_GEMINI_KEY": key}),
            timeout)
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


def ask_rocketride(prompt, pipe_path=PLANNER_PIPE, name="causal-crew-planner", timeout=C.GEMINI_TIMEOUT_S):
    return asyncio.run(_ask(prompt, pipe_path, name, timeout))
