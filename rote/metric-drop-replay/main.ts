#!/usr/bin/env -S rote play run
/**
 * Metric Drop Replay
 *
 * Muscle memory for "revenue dropped, why?". The first investigation is expensive:
 * Causal Crew's LLM planner and investigator agents chose a drill-down path over
 * 65 seconds and 8 decisions. This play replays that learned path on fresh data
 * with zero LLM calls, re-measures every number in SQL, and re-judges it with the
 * same deterministic evidence checks. If the data fails its health check, the
 * replay never runs.
 *
 * @rote-frontmatter
 * ---
 * name: metric-drop-replay
 * description: "Replays a learned revenue-drop investigation with zero LLM calls. Health-checks the orders data first and stops if the number can't be trusted; otherwise follows the drill-down path Causal Crew's LLM agents learned (region=West -> customer_type=new), re-measures share of change, change point, volume vs order value, and the matching changelog note in DuckDB, re-runs noise/seasonality/consistency/timing checks, and reports SUPPORTED or REJECTED plus whether today's data still supports the saved path. Attribution, not causal proof. orders=demo generates seeded demo data."
 * source: https://github.com/PujaSridhar/CausalCrew
 * provenance:
 *   author: pujasridhar28@gmail.com
 *   source_workspace: metric-drop-replay
 * metadata:
 *   contract:
 *     atomic: true
 *     input:
 *       type: none
 *     output:
 *       format: json
 *       destination: stdout
 *     composable: true
 *   rote_version: 0.82.0
 *   version: 0.1.0
 *   status: draft
 *   kind: atomic
 *   flow_type: sequential
 *   execution_model: steps_with_presentation
 *   format: typescript
 *   requires_endpoints: []
 *   requires_sessions: false
 *   discoverability:
 *     tags:
 *     - analytics
 *     - revenue
 *     - root-cause
 *     - duckdb
 *     - agent-memory
 * parameters:
 * - name: orders
 *   param_type: string
 *   required: false
 *   default: demo
 *   description: "Absolute path to an orders CSV or parquet file with columns order_id, date, region, product_category, channel, customer_type, units, revenue. Use demo for seeded demo data or demo-broken for the same data with one day loaded twice."
 * - name: days
 *   param_type: string
 *   required: false
 *   default: "14"
 *   description: "Window length: the latest N days of data are compared with the N days before them"
 * steps:
 *   check_health:
 *     type: process.exec
 *     argv:
 *     - uv
 *     - run
 *     - --no-project
 *     - --with
 *     - duckdb==1.5.5
 *     - --with
 *     - numpy==2.5.3
 *     - --with
 *     - pandas==3.0.5
 *     - --with
 *     - pyarrow==25.0.1
 *     - --with
 *     - python-dotenv==1.2.3
 *     - python3
 *     - "@resource{crew.py}"
 *     - health
 *     - --orders
 *     - $orders
 *     - --days
 *     - $days
 *     - --workspaces
 *     - scratch
 *     timeout_ms: 240000
 *   replay_drilldown:
 *     type: process.exec
 *     depends_on:
 *     - check_health
 *     argv:
 *     - uv
 *     - run
 *     - --no-project
 *     - --with
 *     - duckdb==1.5.5
 *     - --with
 *     - numpy==2.5.3
 *     - --with
 *     - pandas==3.0.5
 *     - --with
 *     - pyarrow==25.0.1
 *     - --with
 *     - python-dotenv==1.2.3
 *     - python3
 *     - "@resource{crew.py}"
 *     - replay
 *     - --recipe
 *     - "@resource{recipes/region-west.json}"
 *     - --orders
 *     - $orders
 *     - --days
 *     - $days
 *     - --workspaces
 *     - scratch
 *     timeout_ms: 240000
 * ---
 */

// check_health exits 1 when the data fails, which blocks replay_drilldown: an
// untrustworthy number is never explained.

const { FlowOutput, isProcessExecBody, loadPresentationContext, stepName } =
  await import("__ROTE_PRESENTATION_SDK__");

type Replay = {
  lead_id: string;
  data: string;
  windows: { baseline: string[]; current: string[] };
  llm_calls: number;
  outcome: string;
  recipe_learned_from?: { question: string; verdict: string; contribution: number };
  segment: Record<string, string>;
  contribution: number | null;
  change_point: string | null;
  volume_rate: {
    orders_base: number;
    orders_current: number;
    aov_base: number;
    aov_current: number;
    volume_share: number | null;
  } | null;
  linked_context: { title: string; date: string; days_from_change_point: number } | null;
  verdict: string;
  checks: Record<string, boolean>;
  path: { dimension: string; value: string; still_supported: boolean }[];
  path_still_supported: boolean;
  queries: number;
  seconds: number;
};

const out = new FlowOutput();
const ctx = await loadPresentationContext();
out.setRunStatus(ctx.run.status);

const pct = (x: number | null | undefined) => (x == null ? "n/a" : `${(x * 100).toFixed(1)}%`);
const money = (x: number) => `$${x.toFixed(2)}`;

const health = ctx.step(stepName("check_health"));
const replay = ctx.step(stepName("replay_drilldown"));

if (replay.outcome.status !== "completed" && replay.outcome.status !== "restored") {
  let reason = "the replay did not complete";
  if (health.outcome.status === "failed") {
    reason = `the data failed its health check, so the replay was not run. ${health.outcome.output.message}`;
  } else if (replay.outcome.status === "failed") {
    reason = `the replay failed: ${replay.outcome.output.message}`;
  }
  out.human(`# Metric drop replay: STOPPED\n\nDON'T TRUST THIS NUMBER YET: ${reason}\n\n` +
    "Run data-health-check on the same file for every check and its evidence.");
  out.summary(`STOPPED: ${reason.split(". ")[0]}`);
  out.result({
    run_id: ctx.run.run_id,
    status: ctx.run.status,
    complete: false,
    outcome: "STOPPED",
    health_step: health.outcome.status,
    replay_step: replay.outcome.status,
    reason,
  });
} else {
  const body = replay.outcome.output.body;
  if (!isProcessExecBody(body)) {
    throw new Error("replay_drilldown did not record a process.exec observation");
  }
  const stdout = body.stdout?.text;
  if (!stdout) throw new Error("replay_drilldown captured no stdout");
  const r = JSON.parse(stdout) as Replay;

  const segment = Object.entries(r.segment).map(([k, v]) => `${k}=${v}`).join(", ");
  const start = r.path.length ? Object.entries(r.segment).find(([k]) => !r.path.some((p) => p.dimension === k)) : null;
  const drill = [start ? `${start[0]}=${start[1]}` : "", ...r.path.map((p) => `${p.dimension}=${p.value}`)]
    .filter(Boolean).join(" -> ");
  const vr = r.volume_rate;
  const learned = r.recipe_learned_from;

  out.human([
    `# Metric drop replay: ${r.verdict}`,
    "",
    learned
      ? `Recipe learned by the LLM crew for "${learned.question}": ${learned.verdict}, ${pct(learned.contribution)} of the change.`
      : `Recipe: ${r.lead_id}`,
    `Replayed on ${r.data}, current ${r.windows.current[0]} to ${r.windows.current[1]} vs baseline ` +
    `${r.windows.baseline[0]} to ${r.windows.baseline[1]}: ${r.llm_calls} LLM calls, ${r.queries} SQL queries, ${r.seconds}s.`,
    "",
    `Finding:        ${segment} accounts for ${pct(r.contribution)} of the revenue change`,
    `Drill-down:     ${drill} (${r.path_still_supported ? "still supported by today's data" : "NO LONGER singled out by today's data; re-run the full investigation"})`,
    `Change point:   ${r.change_point ?? "none found"}`,
    vr
      ? `Volume vs rate: orders ${vr.orders_base.toLocaleString()} -> ${vr.orders_current.toLocaleString()}, ` +
        `average order ${money(vr.aov_base)} -> ${money(vr.aov_current)}; fewer orders explain ${pct(vr.volume_share)} of the change`
      : "Volume vs rate: n/a",
    `Lines up with:  ${r.linked_context ? `${r.linked_context.title} (${r.linked_context.date}, ${r.linked_context.days_from_change_point >= 0 ? "+" : ""}${r.linked_context.days_from_change_point} days)` : "no changelog note near the change point"}`,
    `Evidence:       ${Object.entries(r.checks).map(([k, ok]) => `${k} ${ok ? "pass" : "FAIL"}`).join(", ")}`,
    "",
    `Verdict: ${r.verdict}. Attribution, not proof of causation.`,
  ].join("\n"));
  out.summary(`${r.verdict}: ${segment} accounts for ${pct(r.contribution)}, change point ${r.change_point}, ` +
    `${r.llm_calls} LLM calls in ${r.seconds}s` + (r.path_still_supported ? "" : " (saved path no longer supported)"));
  out.result({
    run_id: ctx.run.run_id,
    status: ctx.run.status,
    complete: ctx.run.status === "succeeded",
    outcome: r.outcome,
    verdict: r.verdict,
    segment: r.segment,
    contribution: r.contribution,
    change_point: r.change_point,
    volume_rate: r.volume_rate,
    linked_context: r.linked_context,
    checks: r.checks,
    path: r.path,
    path_still_supported: r.path_still_supported,
    llm_calls: r.llm_calls,
    queries: r.queries,
    seconds: r.seconds,
    recipe_learned_from: learned ?? null,
  });
}
