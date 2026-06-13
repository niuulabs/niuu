---
type: observation
confidence: high
related_entities: [bjorn-eriksen, astrid-nilsen, valhalla-cloud]
source_ids: [src_b1b2c3d4e5f60109]
---

# Incident: March 2026 database outage

## Compiled Truth

### Key Facts
- On 14 March 2026 production was down for 3 hours 40 minutes; the customer dashboard and telemetry ingestion were both unavailable.
- Root cause: the managed Postgres on Valhalla Cloud stalled mid-failover under disk pressure from an unbounded index build.
- Detection came from a customer report, not internal alerting — disk-pressure alerts were tuned too high.
- Recovery required manually promoting the read replica using the runbook.
- Follow-ups: five-minute migration watchdog rule, halved disk alert thresholds, quarterly failover drills.

### Relationships
- [[bjorn-eriksen]] — led the engineering response and wrote the postmortem.
- [[astrid-nilsen]] — ran incident command.
- [[valhalla-cloud]] — provider whose failover stalled.

### Assessment
The outage was an alerting failure as much as a database failure. The
failover drill cadence is the change most likely to prevent a repeat; the
first drill in April completed promotion in nine minutes.

## Timeline

- 2026-03-14: Outage began at 09:12 CET; customer report at 09:31. [Source: incident channel, slack-#incident, 2026-03-14]
- 2026-03-14: Read replica promoted manually; service restored at 12:52. [Source: incident channel, slack-#incident, 2026-03-14]
- 2026-03-21: Postmortem published with five follow-up actions. [Source: bjorn, wiki, 2026-03-21]
- 2026-04-16: First quarterly failover drill completed in nine minutes. [Source: drill notes, wiki, 2026-04-16]
