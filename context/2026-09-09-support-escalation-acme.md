# Support Escalation — Acme Extraction Failures
Date: 2026-09-09
Author: support
Ticket: SUP-8817

Acme has raised a formal complaint. They report that a large share of
their document extractions are coming back as errors since their
expansion went live, and they are asking whether the platform can
handle their volume.

Current working theory from the support side: the Acme ramp on
2026-09-04 overloaded the extraction path. Their documents are far
larger than our median and their volume tripled overnight. The timing
matches almost exactly.

Proposed mitigation under discussion: rate-limit Acme's extraction
traffic and offer them a batch processing window.

Platform team has not confirmed the diagnosis yet.
