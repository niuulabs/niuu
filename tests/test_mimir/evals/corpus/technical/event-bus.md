---
type: topic
confidence: high
related_entities: [freya-larsen]
source_ids: [src_b1b2c3d4e5f60102]
---

# Event bus

## Compiled Truth

### Key Facts
- All asynchronous messaging between services runs over a NATS JetStream event bus.
- Topics follow `domain.entity.action` naming; every event type is registered in a shared catalog.
- Telemetry from the drone fleet lands on the bus first, then fans out to storage and dashboards.
- Consumers must be idempotent; at-least-once delivery is the contract.

### Relationships
- [[freya-larsen]] — chose NATS over Kafka for operational simplicity.

### Assessment
The bus is the platform's backbone and has not caused an incident since the
consumer-group rebalance fix. The event-type catalog is what keeps forty
services from inventing incompatible payloads.

## Timeline

- 2025-03-10: NATS chosen over Kafka after a two-week spike. [Source: rfc-006, wiki, 2025-03-10]
- 2025-10-14: Consumer-group rebalance bug fixed after duplicate telemetry writes. [Source: changelog, git, 2025-10-14]
- 2026-04-22: Event catalog reached one hundred registered event types. [Source: catalog stats, dashboard, 2026-04-22]
