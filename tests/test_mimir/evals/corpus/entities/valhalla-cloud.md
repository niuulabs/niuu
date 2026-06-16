---
type: entity
confidence: high
entity_type: organization
related_entities: [asgard-robotics]
source_ids: [src_a1b2c3d4e5f60008]
---

# Valhalla Cloud

## Compiled Truth

### Key Facts
- Primary cloud provider for Asgard Robotics; hosts the Kubernetes clusters and the managed Postgres fleet.
- The March 2026 database outage was triggered by their managed Postgres failover misbehaving under disk pressure.
- Provides the object storage used for drone telemetry archives.
- Contract renews annually in November.

### Relationships
- [[asgard-robotics]] — cloud infrastructure provider.

### Assessment
Reliable for compute and storage; the managed Postgres failover behaviour is
the one repeated weakness. The mitigation is our own connection-retry layer
and the read-replica promotion runbook, not switching providers.

## Timeline

- 2024-09-01: Initial cloud contract signed. [Source: contract, drive, 2024-09-01]
- 2026-03-14: Managed Postgres failover stall caused a production outage. [Source: incident channel, slack-#incident, 2026-03-14]
- 2026-04-02: Credited one month of database fees after the outage review. [Source: vendor email, mail, 2026-04-02]
