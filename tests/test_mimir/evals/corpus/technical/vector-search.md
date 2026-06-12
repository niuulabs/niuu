---
type: topic
confidence: medium
related_entities: []
source_ids: [src_b1b2c3d4e5f60103]
---

# Vector search stack

## Compiled Truth

### Key Facts
- Telemetry anomaly search uses hybrid retrieval: keyword full-text search fused with embedding similarity.
- Embeddings come from a sentence-transformers model running on CPU; vectors are stored in Postgres with pgvector.
- Ranking merges the keyword and semantic lists with reciprocal rank fusion.
- Index rebuilds run nightly; incremental updates land within a minute of ingestion.

### Relationships
- (none yet)

### Assessment
Hybrid beats either arm alone in our internal tests, but nobody has measured
quality systematically — there is no golden set. Treat current relevance as
unvalidated.

## Timeline

- 2025-12-09: First hybrid search prototype for anomaly lookup. [Source: spike notes, wiki, 2025-12-09]
- 2026-02-11: pgvector index moved from IVFFlat to HNSW. [Source: changelog, git, 2026-02-11]
