#!/usr/bin/env -S rote play run
/**
 * Data Health Check
 *
 * Is this revenue number real? Six deterministic checks on an orders table before
 * anyone explains a metric move: freshness, row counts, duplicate order ids, days
 * copied under new ids, null spikes, and unit/scale breaks.
 *
 * @rote-frontmatter
 * ---
 * name: data-health-check
 * description: "Is this revenue number real? Runs six deterministic health checks on an orders CSV or parquet file (freshness, row-count anomalies, duplicate order ids, days loaded twice, null spikes, cents-vs-dollars scale breaks) comparing the latest N days with the N before, and says OK or DON'T TRUST THIS NUMBER YET with the evidence and the SQL. No LLM, no keys, reads a local file only. orders=demo generates seeded demo data; orders=demo-broken has one day loaded twice."
 * source: https://github.com/PujaSridhar/CausalCrew
 * provenance:
 *   author: pujasridhar28@gmail.com
 *   source_workspace: data-health-check
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
 *   status: released
 *   kind: atomic
 *   flow_type: sequential
 *   execution_model: steps_with_presentation
 *   format: typescript
 *   requires_endpoints: []
 *   requires_sessions: false
 *   discoverability:
 *     tags:
 *     - data-quality
 *     - analytics
 *     - revenue
 *     - duckdb
 *     - data-observability
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
 *     exit:
 *       accepted_codes: [0, 1]
 *       success_codes: [0, 1]
 * ---
 */

// Exit 1 is an answer, not a fault: the data failed a check and stdout carries the evidence.

const { FlowOutput, isProcessExecBody, loadPresentationContext, stepName } =
  await import("__ROTE_PRESENTATION_SDK__");

type Check = { name: string; ok: boolean; evidence: string; days: string[] };
type Health = {
  data: string;
  windows: { baseline: string[]; current: string[] };
  trust: string;
  status: "PASS" | "FAIL";
  failed: string[];
  checks: Check[];
};

const out = new FlowOutput();
const ctx = await loadPresentationContext();
out.setRunStatus(ctx.run.status);

const step = ctx.requireAvailable(stepName("check_health"));
if (!isProcessExecBody(step.body)) {
  throw new Error("check_health did not record a process.exec observation");
}
const exit = step.body.status.exit;
if (exit.kind !== "code" || (exit.code !== 0 && exit.code !== 1)) {
  throw new Error(`check_health failed: ${step.body.stderr?.text ?? "no stderr captured"}`);
}
const stdout = step.body.stdout?.text;
if (!stdout) throw new Error("check_health captured no stdout");
const h = JSON.parse(stdout) as Health;

const flaggedDays = [...new Set(h.checks.flatMap((c) => (c.ok ? [] : c.days)))].sort();
const passed = h.checks.filter((c) => c.ok).length;
const verdict = h.status === "PASS"
  ? `OK: ${h.data} passed all ${h.checks.length} checks; the change is safe to investigate.`
  : `DON'T TRUST THIS NUMBER YET: ${h.data} failed ${h.failed.join(", ")}` +
    (flaggedDays.length ? ` (${flaggedDays.join(", ")})` : "") +
    ". Fix the data before explaining the change.";

out.human([
  `# Can this number be trusted? ${h.status}`,
  "",
  `Data: ${h.data}`,
  `Current window ${h.windows.current[0]} to ${h.windows.current[1]}, ` +
  `baseline ${h.windows.baseline[0]} to ${h.windows.baseline[1]}`,
  "",
  ...h.checks.map((c) => `  [${c.ok ? " ok " : "FAIL"}] ${c.name.padEnd(15)} ${c.evidence}`),
  "",
  verdict,
].join("\n"));
out.summary(`${h.status}: ${passed}/${h.checks.length} checks passed on ${h.data}` +
  (h.failed.length ? `; failed ${h.failed.join(", ")}` : ""));
out.result({
  run_id: ctx.run.run_id,
  status: ctx.run.status,
  complete: ctx.run.status === "succeeded",
  trust: h.trust,
  health: h.status,
  failed: h.failed,
  flagged_days: flaggedDays,
  data: h.data,
  windows: h.windows,
  checks: h.checks,
});
