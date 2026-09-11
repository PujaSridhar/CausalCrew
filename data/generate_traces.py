"""Generate a synthetic LLM pipeline trace dataset for Causal Crew.

One planted true cause, four plausible decoys. Deterministic given SEED.

True cause
    Prompt v4 (2026-09-05) adds ~1.4k tokens of few-shot examples to the
    system prompt for document_extract and qa_retrieval. On long inputs this
    overruns the context budget, output is truncated, and schema parsing
    fails. Retries fail the same way because the failure is deterministic,
    so cost per successful task roughly doubles.

Decoys
    1. Tenant acme triples volume on 2026-09-04, one day before the deploy,
       and skews toward long documents. Aggregate numbers blame acme.
    2. Cost routing on 2026-09-03 moves classify/summarize to a cheaper
       model. Real cost change, no effect on success rate.
    3. Weekend seasonality. Real dip, too small to explain the drop.
    4. Vendor incident on 2026-09-08 spikes web_fetch latency. Latency only.
"""

import csv
import os
import random
from datetime import datetime, timedelta, timezone

SEED = 20260911
random.seed(SEED)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "traces.csv")

START = datetime(2026, 8, 28, tzinfo=timezone.utc)
DAYS = 14

PROMPT_V4 = datetime(2026, 9, 5, tzinfo=timezone.utc)
ROUTING = datetime(2026, 9, 3, tzinfo=timezone.utc)
ACME_RAMP = datetime(2026, 9, 4, tzinfo=timezone.utc)
VENDOR_BLIP_START = datetime(2026, 9, 8, 14, tzinfo=timezone.utc)
VENDOR_BLIP_END = datetime(2026, 9, 8, 16, 30, tzinfo=timezone.utc)

BASE_VOLUME = 11000

TENANT_WEIGHTS = {
    "acme": 0.18,
    "globex": 0.20,
    "initech": 0.17,
    "umbrella": 0.15,
    "hooli": 0.16,
    "stark": 0.14,
}

DEFAULT_MIX = {
    "document_extract": 0.22,
    "summarize": 0.24,
    "classify": 0.26,
    "qa_retrieval": 0.18,
    "code_review": 0.10,
}

ACME_MIX = {
    "document_extract": 0.55,
    "summarize": 0.15,
    "classify": 0.12,
    "qa_retrieval": 0.13,
    "code_review": 0.05,
}

# Illustrative prices, USD per million tokens. Not vendor quotes.
PRICING = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

BASE_SUCCESS = {
    "document_extract": 0.960,
    "summarize": 0.985,
    "classify": 0.990,
    "qa_retrieval": 0.970,
    "code_review": 0.975,
}

TRANSIENT_REASONS = ["rate_limited", "upstream_5xx", "tool_timeout"]


def weighted(d):
    return random.choices(list(d.keys()), weights=list(d.values()), k=1)[0]


def tenant_weights_for(day):
    w = dict(TENANT_WEIGHTS)
    if day >= ACME_RAMP:
        w["acme"] *= 3.0
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def pick_model(task, day):
    post_routing = day >= ROUTING
    if task == "document_extract":
        return "claude-opus-5" if random.random() < 0.35 else "claude-sonnet-5"
    if task == "qa_retrieval":
        return "claude-sonnet-5"
    if task == "summarize":
        if post_routing and random.random() < 0.45:
            return "claude-haiku-4-5"
        return "claude-sonnet-5"
    if task == "classify":
        if post_routing and random.random() < 0.60:
            return "claude-haiku-4-5"
        return "claude-sonnet-5"
    return "claude-opus-5" if random.random() < 0.40 else "claude-sonnet-5"


def pick_tool_path(task):
    if task == "document_extract":
        return "pdf_parser" if random.random() < 0.80 else "ocr_fallback"
    if task == "qa_retrieval":
        return "vector_search"
    if task == "summarize":
        return "web_fetch" if random.random() < 0.25 else "none"
    if task == "code_review":
        return "repo_fetch"
    return "none"


def pick_doc_pages(task, tenant):
    if task != "document_extract":
        return 0
    # acme skews long, which is what makes the tenant decoy convincing
    if tenant == "acme":
        buckets = [(3, 12, 0.30), (13, 40, 0.28), (41, 120, 0.34), (121, 300, 0.08)]
    else:
        buckets = [(3, 12, 0.44), (13, 40, 0.29), (41, 120, 0.22), (121, 300, 0.05)]
    lo, hi, _ = random.choices(buckets, weights=[b[2] for b in buckets], k=1)[0]
    return random.randint(lo, hi)


def pick_context_chunks(task):
    if task != "qa_retrieval":
        return 0
    return random.choices([4, 8, 12, 18, 24, 32], weights=[18, 24, 26, 16, 11, 5], k=1)[0]


def prompt_version(task, day):
    if task in ("document_extract", "qa_retrieval") and day >= PROMPT_V4:
        return "v4"
    return "v3"


def token_counts(task, pv, doc_pages, chunks):
    base_in = {
        "document_extract": 1800,
        "summarize": 2400,
        "classify": 700,
        "qa_retrieval": 1500,
        "code_review": 3200,
    }[task]
    sys_prompt = 1200 + (1400 if pv == "v4" else 0)
    payload = doc_pages * 420 + chunks * 380
    inp = int((base_in + sys_prompt + payload) * random.uniform(0.88, 1.14))
    base_out = {
        "document_extract": 900,
        "summarize": 650,
        "classify": 90,
        "qa_retrieval": 520,
        "code_review": 1100,
    }[task]
    out = int(base_out * random.uniform(0.7, 1.35))
    return inp, out


def success_probability(task, pv, tool_path, doc_pages, chunks, ts):
    p = BASE_SUCCESS[task]

    # weekend seasonality: real, but small (decoy 3)
    if ts.weekday() >= 5:
        p -= 0.015

    # the planted cause: v4 context overrun on long inputs
    truncation = False
    if pv == "v4":
        if task == "document_extract" and doc_pages > 40 and tool_path == "pdf_parser":
            p = 0.42
            truncation = True
        elif task == "qa_retrieval" and chunks >= 18:
            p = 0.78
            truncation = True
        else:
            p -= 0.003

    # vendor incident: latency, plus a small timeout bump (decoy 4)
    if tool_path == "web_fetch" and VENDOR_BLIP_START <= ts <= VENDOR_BLIP_END:
        p -= 0.06

    return max(0.02, min(0.999, p)), truncation


def latency_for(task, inp, out, tool_path, ts, failed):
    base = 900 + inp * 0.12 + out * 1.6
    if tool_path == "pdf_parser":
        base += 1400
    elif tool_path == "ocr_fallback":
        base += 4200
    elif tool_path == "vector_search":
        base += 320
    elif tool_path == "web_fetch":
        base += 800
    elif tool_path == "repo_fetch":
        base += 1100
    if tool_path == "web_fetch" and VENDOR_BLIP_START <= ts <= VENDOR_BLIP_END:
        base *= 3.4
    if failed:
        base *= random.uniform(0.5, 0.9)
    return int(base * random.uniform(0.8, 1.3))


def cost_for(model, inp, out):
    pin, pout = PRICING[model]
    return round(inp / 1e6 * pin + out / 1e6 * pout, 6)


def main():
    rows = []
    n = 0

    for d in range(DAYS):
        day = START + timedelta(days=d)
        weekend = day.weekday() >= 5
        volume = int(BASE_VOLUME * (0.62 if weekend else 1.0) * random.uniform(0.94, 1.06))
        tw = tenant_weights_for(day)

        for _ in range(volume):
            n += 1
            ts = day + timedelta(seconds=random.randint(0, 86399))
            tenant = weighted(tw)
            mix = ACME_MIX if tenant == "acme" else DEFAULT_MIX
            task = weighted(mix)

            pv = prompt_version(task, day)
            model = pick_model(task, day)
            tool_path = pick_tool_path(task)
            doc_pages = pick_doc_pages(task, tenant)
            chunks = pick_context_chunks(task)

            p, truncation = success_probability(task, pv, tool_path, doc_pages, chunks, ts)

            root_id = f"tr_{n:07d}"
            parent = ""
            attempt = 1

            while True:
                inp, out = token_counts(task, pv, doc_pages, chunks)
                ok = random.random() < p
                if ok:
                    status, reason = "success", ""
                else:
                    if truncation:
                        status, reason = "failure", "schema_parse_error"
                        out = int(out * random.uniform(0.25, 0.55))
                    elif tool_path == "web_fetch" and VENDOR_BLIP_START <= ts <= VENDOR_BLIP_END:
                        status, reason = "failure", "tool_timeout"
                    else:
                        status, reason = "failure", random.choice(TRANSIENT_REASONS)

                trace_id = root_id if attempt == 1 else f"{root_id}_r{attempt - 1}"
                rows.append({
                    "trace_id": trace_id,
                    "ts": ts.isoformat(),
                    "date": day.date().isoformat(),
                    "tenant_id": tenant,
                    "task_type": task,
                    "prompt_version": pv,
                    "model": model,
                    "tool_path": tool_path,
                    "doc_pages": doc_pages,
                    "context_chunks": chunks,
                    "input_tokens": inp,
                    "output_tokens": out,
                    "latency_ms": latency_for(task, inp, out, tool_path, ts, status == "failure"),
                    "cost_usd": cost_for(model, inp, out),
                    "status": status,
                    "failure_reason": reason,
                    "attempt": attempt,
                    "parent_trace_id": parent,
                })

                if status == "success" or attempt >= 3:
                    break
                if random.random() > 0.85:
                    break
                parent = trace_id
                attempt += 1
                ts = ts + timedelta(seconds=random.randint(2, 25))
                # deterministic failures do not heal on retry; transient ones mostly do
                if not truncation:
                    p = 0.80

    fields = list(rows[0].keys())
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows):,} rows -> {OUT_CSV}")

    try:
        import pyarrow as pa
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq
        t = pacsv.read_csv(OUT_CSV)
        pq.write_table(t, os.path.join(HERE, "traces.parquet"))
        print(f"wrote parquet -> {os.path.join(HERE, 'traces.parquet')}")
    except Exception as e:
        print(f"parquet skipped: {e}")


if __name__ == "__main__":
    main()
