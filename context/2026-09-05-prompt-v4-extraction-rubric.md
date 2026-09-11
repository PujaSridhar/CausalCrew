# Prompt v4: Expanded Extraction Rubric
Date: 2026-09-05
Author: applied-ai
PR: #4502

Field-level accuracy on document_extract has been our biggest quality
complaint. v4 addresses it by expanding the system prompt with a
detailed extraction rubric and 12 worked few-shot examples covering the
field types that were most often missed.

Details:

- System prompt grows by approximately 1,400 tokens.
- Applied to document_extract and qa_retrieval.
- Offline eval: field-level F1 improved from 0.87 to 0.93 on the
  standard extraction set.
- classify, summarize, and code_review are untouched.

Rolled out to 100% today. Offline results were strong enough that we
skipped the staged rollout.

Note: the offline eval set is built from documents under 20 pages,
which is where most of our labeled data lives.
