---
type: topic
confidence: medium
related_entities: []
source_ids: [src_d1b2c3d4e5f60303]
---

# LLM cost analysis for report generation

## Compiled Truth

### Key Facts
- Generating customer inspection reports with a large language model costs between 4 and 11 cents per report depending on model tier.
- A small model with a structured template gets within reviewer-acceptable quality for routine reports; only damage-assessment narratives need the large model.
- Routing 80% of reports to the small tier cuts monthly spend by roughly two thirds.
- Caching the static prompt prefix halves input token cost at our volumes.

### Relationships
- (none)

### Assessment
The two-tier routing recommendation is sitting unimplemented because report
volume is still low; revisit when the fleet doubles or the cloud-spend cap
starts to bite.

## Timeline

- 2026-04-24: Cost benchmark run across three model tiers. [Source: research notes, drive, 2026-04-24]
- 2026-05-08: Findings reviewed with the platform team. [Source: meeting notes, drive, 2026-05-08]
