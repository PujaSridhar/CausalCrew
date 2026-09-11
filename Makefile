PY := .venv/bin/python

.PHONY: setup data test check demo-a demo-b

setup:           ## create the venv and install dependencies
	uv venv .venv --python 3.13
	uv pip install --python $(PY) -r requirements.txt

data:            ## generate the demo orders and verify the planted story
	$(PY) data/generate_orders.py
	$(PY) data/verify_orders.py

test:            ## run the unit tests (no network)
	$(PY) -m pytest -q tests

check:           ## smoke-test RocketRide, Gemini, Cognee and CLIs
	$(PY) scripts/check_env.py

demo-a:          ## Run A: broken data, stops at the health check
	$(PY) -m causal_crew.run --broken

demo-b:          ## Run B: clean data, full investigation
	$(PY) -m causal_crew.run
