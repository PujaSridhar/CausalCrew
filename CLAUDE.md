# Causal Crew — project context

Hackathon: Data & AI Hackathon, AWS Builder Loft SF, Sept 11 2026. Solo build. Coding window: 11:00 AM – 3:30 PM (submission deadline 3:30 PM sharp).

## Problem statement

When a key business metric moves ("revenue dropped 15% last week, why?"), two very different things could be going on:

1. The number is wrong — the data broke (a day loaded twice, a feed stopped, a unit change). The business is fine; the dashboard is lying.
2. The number is right — something real happened in the business. Finding out what means following several leads (a region, a product, a channel, a pricing change), and each lead needs its own deep drill-down. Investigated one at a time, this takes days, and whoever investigates anchors on the first plausible explanation and stops digging.

Causal Crew first checks whether the number can be trusted. If it can't, it stops and says why. If it can, it gives every lead its own investigator agent with its own workspace, so competing explanations are each pursued in depth and judged on measured evidence, not on which one was found first. Company context guides which leads to pursue, and every verified finding becomes memory for the next question.

One-liner: "Is the number real? If yes, every lead gets its own investigator — and the evidence decides."

## Why multi-agent (must stay true in the build)

* Depth per lead: each lead needs a multi-step investigation (drill-downs, change-point detection, volume vs rate split, sub-segment checks, context matching). One agent juggling several leads at that depth gets a muddled context and shallow answers.
* Independence: each investigator sees only its own lead, so no finding is biased by another. Evidence is compared at the end on equal footing.
* Own workspace per lead: each investigator creates its own scratch tables and intermediate results; every lead's evidence is self-contained and auditable.

If an investigator only runs one GROUP BY, this justification is false. The depth requirements below are mandatory, not polish.

## What we are NOT

* Not a pipeline fixer. We detect broken data and stop; we never repair it.
* Not a causal-proof engine. We do attribution: which segments account for the change, by how much, and what context lines up. Never claim proven causation.

## Architecture

```
 Question (metric, current window, baseline window)
        │
        ▼
 [1] HEALTH CHECK  (deterministic, no LLM)
        │  FAIL → "don't trust this number yet" + evidence → STOP
        │  PASS
        ▼
 [2] CONTEXT  (Cognee: changelogs, release notes, incident notes)
        │
        ▼
 [3] PLANNER  (LLM: turns data dimensions + context into 3–4 leads)
        │
   ┌────┼────────────┬────────────┐
   ▼    ▼            ▼            ▼
 [4] INVESTIGATOR AGENTS — one per lead, run in parallel
     each: own Hotdata database, own context, deep multi-step drill-down
   └────┬────────────┴────────────┘
        ▼
 [5] JUDGE  (deterministic: evidence checks, overlap merge, ranking, verdicts)
        │
        ▼
 [6] REPORT  →  [7] MEMORY WRITE-BACK (Cognee)
```

Orchestration: stages run as a RocketRide pipeline; investigators fan out in parallel and results are collected before the judge.

## Component specs

### [1] Health check — deterministic, PASS/FAIL + evidence

* Freshness: max(date) equals the expected latest date.
* Row counts: flag a day in the current window only if it is beyond ±3 robust z-scores (median/MAD) of the trailing 28 days **and** more than 25% off the trailing median. (Robust z alone flags the planted, real West drop; see Decisions.)
* Duplicates: zero duplicate `order_id`s; flag any day whose rows duplicate another day.
* Null spikes: key-column null rate rises ≤ 5 percentage points vs baseline.
* Scale break: median order value ratio (current/baseline) outside [0.2, 5] → possible unit change (cents vs dollars).

Any failure → stop and report which check failed, with the numbers.

### [3] Planner

* Inputs: the question, available dimensions (region, product_category, channel, customer_type), and context events near the change window.
* Output: 3–4 leads, each a short hypothesis + the starting segment, e.g. "West region, possibly the shipping fee change on date D".
* Must include data-driven leads (largest segment deltas), not only context-driven ones, so the context can't bias which leads exist.

### [4] Investigator agent (same code, one instance per lead)

Each instance works only on its lead, in its own Hotdata database. LLM decides the path; all numbers come from SQL/Python. Minimum depth per lead:

1. Size the lead: segment delta and contribution share (segment delta / total delta).
2. Drill down at least 2 levels inside the segment (e.g. West → category → customer type), following the largest deltas.
3. Change point: find when the segment's daily series shifted.
4. Volume vs rate: revenue = orders × avg order value; how much is fewer orders vs smaller orders.
5. Context match: does a context event line up with the change point?

Output a structured finding: segment definition, contribution %, drill-down path, change-point date, volume/rate split, linked context, and every SQL query used.

### [5] Judge — deterministic, the LLM never writes verdicts

Per finding:

1. Noise: ≥ 200 orders in each window; bootstrap 95% CI of the delta excludes 0.
2. Seasonality: the same comparison one year earlier did not show a similar change (within 50% of the current effect).
3. Consistency: the effect holds within sub-segments, not only in aggregate (guards against Simpson's paradox).
4. Timing: if context is linked, the change point is within ±3 days of the event date.

Across findings:

* Overlap merge: if two leads explain largely the same orders (> 50% overlap), merge them into one finding instead of double-counting.
* Rank by contribution share.

Verdicts (pure Python, unit-tested): default `INSUFFICIENT_EVIDENCE`; `SUPPORTED` if checks 1–3 pass and contribution ≥ 30%; add `CONTEXT_LINKED` if check 4 passes; `REJECTED` with the failing check named otherwise.

### [6] Report

Health-check result, ranked findings with verdicts, key numbers, drill-down path, linked context, and the SQL behind every number. No LLM-generated numbers.

## Tool roles (each must earn its place)

* RocketRide: orchestration; runs the pipeline and the parallel fan-out. Submission needs the exported `.pipe` files.
* Hotdata: one database per investigator, holding that lead's scratch tables and intermediate results.
* Cognee: turns unstructured context into searchable knowledge for the planner and investigators; stores verified findings for future questions.
* Snyk: dev hygiene only; scan the repo before submission. Not in the pitch.

Do not guess any sponsor API. Read the official docs / workshop scaffolds, or ask me. If an API behaves differently than expected, stop and tell me.

## Demo data (synthetic, seeded, generated by script)

* `orders`: order_id, date, region (West/East/South/North), product_category, channel (web/app/email), customer_type (new/returning), units, revenue. ~13 months of daily data so the seasonality check works.
* Planted real cause: West flat shipping fee raised $4.99 → $8.99 on date D; West orders drop ~30% after D, concentrated in new customers.
* Planted decoy: email campaign ended on D−2; small email-channel dip across all regions. Should get low contribution or be REJECTED.
* Broken copy for demo run A: same data with one day loaded twice.
* Context docs: 8–10 changelog/release-note entries in markdown, including the shipping-fee change and the campaign end, plus unrelated noise entries.

Demo script:

* Run A (broken data) → health check fails, names the duplicated day, stops.
* Run B (clean data) → investigators run in parallel; the West lead drills down to new customers after date D and links the shipping-fee entry → SUPPORTED + CONTEXT_LINKED; the email decoy is rejected.
* Say out loud that the data is synthetic with a planted cause.

## Build order and cut lines

1. 11:00–12:00 — Data in Hotdata; ONE investigator on ONE lead end to end through RocketRide. Most important hour.
2. 12:00–1:00 — Full investigator depth (steps 1–5) for that single lead.
3. 1:00–1:45 — Planner + parallel fan-out to 3–4 investigators, one DB each. Fallback if parallelism breaks by 2:00: run the same investigators sequentially (separate contexts and DBs still hold).
4. 1:45–2:15 — Health check + broken-data run.
5. 2:15–2:45 — Judge: checks 1–3, overlap merge, verdicts, tests. Cut check 4 and overlap merge first if short on time.
6. 2:45–3:00 — Cognee context + write-back. Fallback: read the changelog markdown directly.
7. 3:00–3:15 — FEATURE FREEZE. Record backup demo video, Snyk scan, README.
8. 3:15–3:30 — Submit: GitHub link + all `.pipe` files in RocketRide Discord #showcase, plus any other form announced at the event.

## Decisions made during the build (Sept 11)

These resolve points the spec left open or that the data showed were wrong. All live in `causal_crew/config.py`.

* Row counts use a 25% relative floor on top of robust z (`ROW_COUNT_MIN_REL_DEV`).
* "A day whose rows duplicate another day" is a separate `duplicate_days` check: identical row content under a different date, whatever the order ids.
* Seasonality compares percent changes, so year-over-year growth doesn't shrink last year's effect.
* Consistency: within the finding's segment, split by each dimension not in it; pass if sub-segments holding ≥80% of baseline revenue move with the aggregate.
* Bootstrap resamples days, not orders.
* Checks pass but contribution < 30% → `INSUFFICIENT_EVIDENCE`. Overlap is measured on lost orders.
* LLM decides the path, within limits: a sub-segment qualifies only if its share of the change is ≥1.25× its share of baseline revenue. The investigator's LLM (via RocketRide) chooses among qualifying sub-segments; the rule overrides unsupported choices and covers LLM outages, and rationales citing numbers not in the evidence are withheld.
* Investigators use one local DuckDB per lead behind `Workspace`; Hotdata signup required a credit card at the event. Hotdata `fork` per investigator is the drop-in upgrade.
* RocketRide runs every agent's LLM step on the staging server: the planner and each investigator, each as its own task with a unique project id (RocketRide allows one running task per project). Investigators' SQL runs locally in their own DuckDB workspaces.
* Changelog notes whose title names a segment always become leads, so the LLM can't drop a relevant note; the LLM fills the remaining lead slots.
* Memory: SUPPORTED findings go to `memory/findings.jsonl` and back into the planner prompt; Cognee write-back is opt-in (`--remember`) because Gemini's free tier (20 requests/model/day) can't sustain Cognee during a live demo.

## Working rules for Claude Code

* Keep a working end-to-end path at all times; commit after every working step.
* Deterministic logic (health checks, math, judge, verdicts) lives in plain Python with unit tests. The LLM plans and explains; it never produces numbers or verdicts.
* Investigators share code but never share state; each gets only its own lead, its own DB, and the context relevant to it.
* Thresholds live in one config file so they're easy to tune.
* Prefer simple and boring over clever. No new frameworks mid-build.
* Never claim causation; use "explains", "accounts for", "lines up with".
* Before 3:00 PM, flag anything that risks the demo path instead of silently adding scope.
