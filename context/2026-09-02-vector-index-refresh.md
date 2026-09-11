# Vector Index Refresh — qa_retrieval
Date: 2026-09-02
Author: search-infra
PR: #4471

Rebuilt the embedding index for the qa_retrieval corpus after the
August document import. Index size grew roughly 18%. Recall@10 on the
internal eval set improved from 0.81 to 0.84.

No change to retrieval parameters. Default chunk count per query stays
at 12. Rollout was instant and reversible.
