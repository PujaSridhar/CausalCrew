"""Sanity-check that the planted cause and the decoys read the way we want."""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_parquet(os.path.join(HERE, "traces.parquet"))
df["date"] = df["date"].astype(str)
df["root"] = df["trace_id"].str.split("_r").str[0]

# a task succeeds if any attempt succeeded
task = (df.sort_values("attempt")
          .groupby("root")
          .agg(date=("date", "first"),
               tenant_id=("tenant_id", "first"),
               task_type=("task_type", "first"),
               prompt_version=("prompt_version", "first"),
               tool_path=("tool_path", "first"),
               doc_pages=("doc_pages", "first"),
               attempts=("attempt", "max"),
               ok=("status", lambda s: (s == "success").any())))
cost = df.groupby("root")["cost_usd"].sum()
task["cost_usd"] = cost

pd.set_option("display.width", 140)

print("=== headline: daily task success rate + cost per successful task ===")
daily = task.groupby("date").agg(tasks=("ok", "size"),
                                 success_rate=("ok", "mean"),
                                 total_cost=("cost_usd", "sum"))
daily["cost_per_success"] = daily.total_cost / (daily.tasks * daily.success_rate)
print(daily[["tasks", "success_rate", "cost_per_success"]].round(4).to_string())

pre = task[task.date < "2026-09-05"]
post = task[task.date >= "2026-09-05"]
def cps(t):
    return t.cost_usd.sum() / t.ok.sum()
print(f"\npre-v4  success={pre.ok.mean():.4f}  cost/success=${cps(pre):.4f}")
print(f"post-v4 success={post.ok.mean():.4f}  cost/success=${cps(post):.4f}")
print(f"delta   success={post.ok.mean()-pre.ok.mean():+.4f}  cost/success={cps(post)/cps(pre)-1:+.1%}")

print("\n=== TRUE CAUSE: document_extract via pdf_parser, by doc length ===")
de = task[(task.task_type == "document_extract") & (task.tool_path == "pdf_parser")].copy()
de["long_doc"] = de.doc_pages > 40
print(de.groupby(["long_doc", "prompt_version"]).ok.agg(["size", "mean"]).round(4).to_string())

print("\n=== TRUE CAUSE: qa_retrieval by prompt_version ===")
qa = task[task.task_type == "qa_retrieval"]
print(qa.groupby("prompt_version").ok.agg(["size", "mean"]).round(4).to_string())

print("\n=== DECOY 1: tenant success rate, pre vs post ===")
t = task.copy()
t["era"] = (t.date >= "2026-09-05").map({True: "post", False: "pre"})
print(t.pivot_table(index="tenant_id", columns="era", values="ok", aggfunc="mean").round(4).to_string())

print("\n--- same cohort (long docs, pdf_parser): acme vs everyone else ---")
d2 = de.copy()
d2["era"] = (d2.date >= "2026-09-05").map({True: "post", False: "pre"})
d2["who"] = (d2.tenant_id == "acme").map({True: "acme", False: "other"})
print(d2[d2.long_doc].pivot_table(index="who", columns="era", values="ok", aggfunc="mean").round(4).to_string())
print("(if these two rows collapse by the same amount, the tenant is exonerated)")

print("\n=== DECOY 2: routing change -- cost down, success flat ===")
r = df[df.task_type.isin(["classify", "summarize"])].copy()
r["era"] = (r.date >= "2026-09-03").map({True: "post", False: "pre"})
print(r.groupby(["era", "model"]).agg(calls=("cost_usd", "size"), cost=("cost_usd", "mean")).round(6).to_string())
rt = task[task.task_type.isin(["classify", "summarize"])].copy()
rt["era"] = (rt.date >= "2026-09-03").map({True: "post", False: "pre"})
print("\nsuccess rate by era:", rt.groupby("era").ok.mean().round(4).to_dict())

print("\n=== DECOY 4: vendor blip -- latency up, success barely moves ===")
wf = df[(df.tool_path == "web_fetch") & (df.date == "2026-09-08")].copy()
wf["hour"] = pd.to_datetime(wf.ts).dt.hour
print(wf.groupby("hour").latency_ms.mean().round(0).to_string())

print("\n=== retries: deterministic failures do not heal ===")
f = df[df.status == "failure"]
print(f.failure_reason.value_counts().to_string())
