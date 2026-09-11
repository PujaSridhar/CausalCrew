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

## Status

Pre-build prep only. What exists today:

- `data/generate_orders.py` — the demo dataset generator
- `data/verify_orders.py` — a throwaway oracle that checks the demo data reads as designed
- `context/` — ten changelog and incident notes for Cognee
- `causal_crew/config.py` — every threshold in one place

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
