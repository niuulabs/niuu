---
type: directive
confidence: high
related_entities: [valhalla-cloud]
source_ids: [src_b1b2c3d4e5f60104]
---

# Postgres migration rules

## Compiled Truth

### Key Facts
- Database schema changes ship as paired up/down SQL migration files with zero-padded sequence numbers.
- Migrations must be idempotent: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS.
- Schema changes must be backwards-compatible one release in each direction.
- Migrations run via the Kubernetes-native migrate job, never by hand against production.
- The managed Postgres on Valhalla Cloud requires migrations to finish in under five minutes or the failover watchdog intervenes.

### Relationships
- [[valhalla-cloud]] — hosts the managed Postgres fleet these rules protect.

### Assessment
The five-minute watchdog constraint is the rule people forget; the March
outage review added it after a long-running index build stalled a failover.

## Timeline

- 2025-05-02: Migration rules first written down. [Source: rfc-009, wiki, 2025-05-02]
- 2026-03-21: Five-minute rule added after the outage postmortem. [Source: postmortem, wiki, 2026-03-21]
