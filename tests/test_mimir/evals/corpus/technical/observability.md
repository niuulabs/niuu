---
type: topic
confidence: medium
related_entities: [bjorn-eriksen]
source_ids: [src_b1b2c3d4e5f60106]
---

# Observability

## Compiled Truth

### Key Facts
- Metrics go to Prometheus, traces to Tempo, logs to Loki; Grafana is the single pane of glass.
- Every service exposes the four golden signals: latency, traffic, errors, saturation.
- Drone fleet telemetry has its own retention tier: thirty days hot, two years in object storage.
- Alert routing pages the on-call through the escalation policy; Slack alerts are advisory only.

### Relationships
- [[bjorn-eriksen]] — built the fleet telemetry dashboards for Helios.

### Assessment
Coverage is good for services, thin for batch jobs. The March outage was
detected by customer report before our own alerting fired — the disk-pressure
alert threshold has since been halved.

## Timeline

- 2025-07-08: Grafana stack consolidated into one workspace. [Source: changelog, git, 2025-07-08]
- 2026-03-21: Disk-pressure alert thresholds halved after the outage. [Source: postmortem, wiki, 2026-03-21]
