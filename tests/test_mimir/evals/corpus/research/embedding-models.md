---
type: topic
confidence: medium
related_entities: []
source_ids: [src_d1b2c3d4e5f60301]
---

# Embedding model comparison

## Compiled Truth

### Key Facts
- Compared four sentence-embedding models for telemetry anomaly search: MiniLM, mpnet, a multilingual model, and a code-specialised model.
- MiniLM gives the best latency-per-quality on CPU: 384 dimensions, under 10 ms per sentence.
- mpnet wins on raw retrieval quality but costs three times the inference latency.
- Multilingual mattered less than expected — operator notes are overwhelmingly English.
- Recommendation: MiniLM for interactive search, mpnet for nightly batch enrichment.

### Relationships
- (none)

### Assessment
The split recommendation (fast model interactive, big model batch) mirrors
what the dashboard team already does with map tiles. Revisit if GPU inference
becomes available on the edge boxes.

## Timeline

- 2026-03-18: Benchmark suite run across the four candidates. [Source: research notes, drive, 2026-03-18]
- 2026-04-03: Recommendation adopted for the anomaly search prototype. [Source: spike review, wiki, 2026-04-03]
