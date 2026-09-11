# Demo runbook

**Say early: the data is synthetic, with a planted cause.** That is a strength, not a caveat:
it is how the demo proves the judge rejects the decoy.

## Before you start

```bash
make app        # leave running; open http://localhost:8000
```

Have a second terminal ready with `make demo-a` / `make demo-b` in case the browser misbehaves.

## Three minutes

| Time | Do | Say |
|---|---|---|
| 0:00 | The home screen, question box in view | "A number moved. Either the number is wrong, or something real happened. Most tools skip the first question." |
| 0:20 | Pick **Orders, broken feed**, ask the default question, Investigate | "Same question, on a feed where one day was loaded twice." |
| 0:40 | The red banner | "It stops. Sep 3 is in there twice, 1,433 duplicate orders. We never explain a number we can't trust." |
| 1:00 | Switch to **Orders**, Investigate again | "Clean data. The planner picks leads from the data itself, plus the changelog, and every lead gets its own investigator with its own database." |
| 1:20 | Point at the crew while it runs | "Four investigators in parallel. Each one's drill-down path is chosen by Gemini running on RocketRide, but only among sub-segments the statistics support." |
| 1:45 | The answer card | "New customers in the West explain 59% of the drop, starting Aug 27, the day the West shipping fee went up. It's fewer orders, not smaller ones." |
| 2:05 | Same card, the rejection line | "The email campaign ended two days earlier and looks guilty. The judge rejects it: the same dip happened in these weeks last year." |
| 2:25 | **Findings** tab: drill-down path, evidence checks, then **SQL** tab | "Every number comes from SQL, in that agent's own workspace. Verdicts are deterministic checks, not the model's opinion." |
| 2:45 | **Memory** tab, then the sidebar | "Verified findings go into memory, so the next question starts smarter." |

Close with: **"Attribution, not causation. It tells you which segments account for the change, and what lines up with it."**

## If something breaks

| Problem | Do this |
|---|---|
| A run hangs or Wi-Fi dies | Click any past run in the sidebar. It replays instantly, with no LLM call. |
| The planner says "keyword fallback" | Nothing is wrong; say so. "The LLM is down, so it fell back to the deterministic path, and the answer is the same." |
| The browser misbehaves | `make demo-a` and `make demo-b` in the terminal, then open `reports/run_b_clean.md`. |
| Someone asks for the raw evidence | `reports/run_b_clean.md` has the SQL behind every number. |

## Questions judges ask

- **"Why multi-agent, really?"** Each lead needs a multi-step drill-down. One agent juggling four leads gets a muddled context and anchors on the first story. Each investigator sees only its own lead and writes its own scratch tables, so findings can't bias each other and each one is auditable alone.
- **"What's real and what's planted?"** The data is generated (`data/generate_orders.py`, seed 77) with one real cause and one decoy; `PLANTED_CAUSE.md` documents both. The pipeline knows nothing about either.
- **"Isn't the LLM just guessing?"** It proposes leads and picks drill-down directions among options the statistics already allow. Every number is SQL, every verdict is a deterministic check, and a rationale citing a number not in the evidence is dropped.
- **"Why DuckDB and not Hotdata?"** Hotdata signup needed a credit card at the event. The `Workspace` class is the seam; one forked instant database per investigator is the drop-in upgrade.
- **"Where does this go?"** Your own data (CSV, then a warehouse), metrics beyond revenue, and monitoring that investigates on its own when a number moves.

## Submit

- [ ] GitHub link: https://github.com/PujaSridhar/CausalCrew
- [ ] `pipelines/planner.pipe` and `pipelines/investigator.pipe`
- [ ] Post in RocketRide Discord **#showcase**, plus any form announced at the event
- [ ] Mention: RocketRide runs every agent's LLM step; Cognee holds the changelog knowledge graph; Snyk scans dependencies and code
