# Causal Crew

**Is the number real? If yes, every lead gets its own investigator — and the evidence decides.**

When a business metric moves, Causal Crew first checks whether the number can
be trusted. If the data is broken — a day loaded twice, a feed that stopped, a
unit change — it stops and says which check failed. If the data is sound, a
planner turns the data and company context into competing leads, and each lead
gets its own investigator agent with its own isolated database. A
deterministic judge weighs the evidence and hands down verdicts. Verified
findings become memory for the next question.

Causal Crew does attribution, not causal proof: it reports which segments
account for a change, by how much, and what context lines up with it.

See [CLAUDE.md](CLAUDE.md) for the full spec.

## Pipeline

1. **Health check** — deterministic PASS/FAIL on freshness, row counts, duplicates, null spikes, and scale breaks.
2. **Context** — Cognee turns changelogs and incident notes into searchable knowledge.
3. **Planner** — proposes 3–4 leads, both data-driven and context-driven.
4. **Investigators** — one per lead, in parallel, each in its own Hotdata database.
5. **Judge** — deterministic checks for noise, seasonality, consistency, and timing; overlap merge; verdicts.
6. **Report** — every number with the SQL behind it.
7. **Memory** — findings written back to Cognee.

Orchestrated as a RocketRide pipeline.

## Run the demo

```bash
.venv/bin/python -m causal_crew.run --broken   # Run A: health check fails, names the duplicated day, stops
.venv/bin/python -m causal_crew.run            # Run B: planner, parallel investigators, judge, report
```

Reports land in `reports/` as Markdown and JSON, with the SQL behind every number.

## What's built

| Stage | Module | Notes |
|---|---|---|
| Health check | `causal_crew/health.py` | freshness, row counts, duplicates, null spikes, scale break |
| Planner | `causal_crew/planner.py` | data-driven leads from SQL; Gemini adds context leads; title-keyword fallback if Gemini is down |
| Investigator | `causal_crew/investigator.py`, `workspace.py` | one isolated DuckDB database per lead, behind a small `Workspace` interface. Hotdata instant databases with one fork per investigator are the drop-in upgrade; account signup needed a credit card at the event, so the demo runs on DuckDB |
| Judge | `causal_crew/judge.py` | noise, seasonality, consistency, timing, overlap merge, verdicts |
| Runner + report | `causal_crew/run.py` | stages in order, investigators in parallel |
| Memory | `scripts/ingest_context.py` | loads context notes into Cognee; the planner reads the notes directly as the spec's fallback |
| Orchestration | RocketRide, `pipelines/planner.pipe` | the planner's Gemini call runs as a RocketRide pipeline on staging; direct Gemini, then a keyword match, are fallbacks |

## Setup

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt
brew install hotdata-dev/tap/cli snyk-cli
cp .env.example .env    # then fill in keys
hotdata auth login      # opens a browser
snyk auth               # opens a browser
.venv/bin/python scripts/check_env.py   # PASS/FAIL/SKIP per service
```

## Demo data

The data is **synthetic**, with a planted cause. See
[PLANTED_CAUSE.md](PLANTED_CAUSE.md) for exactly what was planted.

```bash
.venv/bin/python data/generate_orders.py   # ~10s, deterministic
.venv/bin/python data/verify_orders.py     # exits non-zero if the demo wouldn't read as designed
```

Outputs `data/orders.{parquet,csv}` (clean) and `data/orders_broken.{parquet,csv}`
(one day loaded twice). Generated files are gitignored.

Schema: `order_id, date, region, product_category, channel, customer_type, units, revenue`
