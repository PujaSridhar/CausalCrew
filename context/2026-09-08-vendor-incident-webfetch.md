# Vendor Incident — web_fetch Upstream Degradation
Date: 2026-09-08
Author: oncall
Incident: INC-2291

Our web_fetch content provider degraded between roughly 14:00 and
16:30 UTC. Their status page confirmed elevated error rates in the
us-west region.

Impact on us:

- web_fetch latency rose to roughly 3x normal for the duration.
- A small number of tool_timeout failures on summarize tasks that use
  the web_fetch path.
- No impact on document_extract, qa_retrieval, classify, or
  code_review, none of which touch this provider.

Recovered on its own. No action taken on our side. Closed.
