# Causal Crew

**A hypothesis swarm that explains why an AI pipeline regressed.**

A metric moves. A planner agent reads the changelog and proposes competing
hypotheses for why. Each hypothesis is handed to its own agent with its own
isolated database, where it builds the derived tables it needs to test that
hypothesis and nothing else. A judge ranks the surviving hypotheses by
measured effect size. Findings are written back to memory so the next
investigation starts smarter.

Causal Crew hunts causal *leads*. Every lead is backed by a measured effect
size and linked to the change record that explains it. It does not claim to
prove causation.

## The domain: AI pipeline observability

The dataset is LLM request traces — every call the pipeline made, with model,
prompt version, tool path, tokens, latency, cost, and outcome. The question is
the one every AI team has lost a weekend to: *our success rate dropped and our
cost per task jumped, and the dashboard is pointing at the wrong thing.*

## Architecture

| Layer | Tool | Job |
|---|---|---|
| Orchestration | RocketRide | Planner, the parallel hypothesis agents, and the judge run as pipeline steps |
| Isolated compute | Hotdata | One database per hypothesis agent — each writes its own derived tables |
| Memory | Cognee | Ingests the changelog so the planner proposes grounded hypotheses, then stores findings |
| Security | Snyk | Scans the repo and its dependencies |

**Why the isolation is real:** these agents *write*. Each one builds its own
cohort tables, filtered slices, and intermediate aggregates to test its own
hypothesis. Pointed at one shared database they collide on table names and
leave each other's scratch state behind. A database per hypothesis is the
whole point.

## The dataset

`data/generate_traces.py` produces 146,788 trace rows across 14 days
(2026-08-28 to 2026-09-10), deterministic under a fixed seed.

```bash
python3 data/generate_traces.py
python3 data/verify_plant.py
```

Outputs `data/traces.csv` (19MB, for ingest) and `data/traces.parquet` (3.9MB).

Schema: `trace_id, ts, date, tenant_id, task_type, prompt_version, model,
tool_path, doc_pages, context_chunks, input_tokens, output_tokens, latency_ms,
cost_usd, status, failure_reason, attempt, parent_trace_id`

`context/` holds the changelog corpus for Cognee: eight documents covering
deploys, a routing change, a customer expansion, an on-call handoff, a vendor
incident, and a support escalation.

See `PLANTED_CAUSE.md` for what was planted and why. It is checked in on
purpose — the demo dataset is synthetic and says so.

## What makes the demo land

The obvious answer is wrong, and it is wrong in a way you can measure.

Aggregate numbers point straight at tenant Acme, whose volume tripled one day
before the real cause shipped. The on-call engineer blamed Acme. Support filed
an escalation blaming Acme and proposed rate-limiting them. Every dashboard
would agree.

Acme is innocent. Slice to the affected cohort and Acme's failure rate and
everyone else's collapse by the same amount on the same day — the day prompt
v4 shipped. Causal Crew rejects the tenant hypothesis with a number instead of
an opinion.
