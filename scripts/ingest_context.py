"""Load every context note into Cognee so the planner and investigators can search it.

One note takes ~45s on Gemini, so run this ahead of the demo, not during it:

    .venv/bin/python scripts/ingest_context.py
"""

import asyncio
import glob
import os
import time

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

import cognee  # noqa: E402  (reads LLM/embedding settings from the environment on import)


async def main():
    paths = sorted(glob.glob(os.path.join(ROOT, "context", "*.md")))
    start = time.time()
    for i, path in enumerate(paths, 1):
        t = time.time()
        with open(path) as f:
            await cognee.remember(f.read())
        print(f"[{i}/{len(paths)}] {os.path.basename(path)} ({time.time() - t:.0f}s)", flush=True)
    print(f"done: {len(paths)} notes in {time.time() - start:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
