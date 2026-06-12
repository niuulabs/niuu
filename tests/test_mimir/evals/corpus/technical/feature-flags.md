---
type: topic
confidence: medium
related_entities: []
source_ids: [src_b1b2c3d4e5f60110]
---

# Feature flags

## Compiled Truth

### Key Facts
- Feature flags live in a config service, evaluated server-side; the SPA receives resolved values only.
- Every flag has an owner and an expiry date; the linter fails CI when a flag outlives its expiry.
- Kill switches for telemetry ingestion and dashboard streaming are permanent operational flags, exempt from expiry.

### Relationships
- (none)

### Assessment
Flag hygiene is genuinely enforced — the expiry linter has deleted more dead
code than any refactor this year.

## Timeline

- 2025-09-29: Flag expiry linting added to CI. [Source: changelog, git, 2025-09-29]
- 2026-03-14: Telemetry kill switch used during the database outage. [Source: incident channel, slack-#incident, 2026-03-14]
