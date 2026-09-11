PY := .venv/bin/python

.PHONY: setup setup-cognee data test check scan demo-a demo-b app

setup:           ## create the venv (with pip, which Snyk needs) and install core dependencies
	uv venv --seed .venv --python 3.13
	uv pip install --python $(PY) -r requirements.txt

setup-cognee:    ## optional: Cognee memory integration (see README > Security)
	uv pip install --python $(PY) -r requirements-cognee.txt

scan:            ## Snyk dependency scan of the core requirements
	snyk test --file=requirements.txt --command=$(PY) --package-manager=pip

data:            ## generate the demo orders and verify the planted story
	$(PY) data/generate_orders.py
	$(PY) data/verify_orders.py

test:            ## lint and run the unit tests (no network)
	.venv/bin/ruff check .
	$(PY) -m pytest -q tests

check:           ## smoke-test RocketRide, Gemini, Cognee and CLIs
	$(PY) scripts/check_env.py

demo-a:          ## Run A: broken data, stops at the health check
	$(PY) -m causal_crew.run --broken

demo-b:          ## Run B: clean data, full investigation
	$(PY) -m causal_crew.run

app:             ## web dashboard at http://localhost:8000
	$(PY) -m uvicorn causal_crew.web.app:app --port 8000
