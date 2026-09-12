"""Assemble a Rote Play package's resources/ from this repo.

    .venv/bin/python scripts/build_play_resources.py rote/metric-drop-replay/resources

A Play runs on a stranger's machine, so everything its process steps need is
packaged: the deterministic Causal Crew modules, the seeded demo-data generator,
the changelog notes, and a recipe. No keys, no data files (the generator makes
the demo data at run time; users pass their own orders file instead).
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ("__init__", "config", "health", "investigator", "judge", "llm", "memory", "plays",
           "segments", "workspace")


def build(dest):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    pkg = os.path.join(dest, "causal_crew")
    os.makedirs(pkg)
    for m in MODULES:
        shutil.copy(os.path.join(ROOT, "causal_crew", f"{m}.py"), pkg)
    shutil.copytree(os.path.join(ROOT, "context"), os.path.join(dest, "context"))
    shutil.copy(os.path.join(ROOT, "data", "generate_orders.py"), dest)
    recipes = os.path.join(ROOT, "plays", "recipes")
    if os.path.isdir(recipes):
        shutil.copytree(recipes, os.path.join(dest, "recipes"))
    shutil.copy(os.path.join(ROOT, "plays", "crew.py"), dest)
    return sorted(os.path.relpath(os.path.join(d, f), dest) for d, _, fs in os.walk(dest) for f in fs)


if __name__ == "__main__":
    for f in build(sys.argv[1]):
        print(f)
