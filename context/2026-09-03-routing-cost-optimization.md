# Model Routing: Cost Optimization for classify and summarize
Date: 2026-09-03
Author: platform-eng
PR: #4488

We are spending more than we need to on two high-volume, low-complexity
task types. Offline evaluation over 5,000 labeled samples showed
claude-haiku-4-5 matching claude-sonnet-5 within noise on both.

Change: route a share of traffic to claude-haiku-4-5.

- classify: 60% of traffic
- summarize: 45% of traffic
- document_extract, qa_retrieval, code_review: unchanged

Expected effect: meaningful reduction in cost per call on the routed
task types. No expected effect on success rate. We will watch quality
metrics for a week before widening the split.
