---
type: observation
confidence: high
related_entities: [bjorn-eriksen, nordvolt]
source_ids: [src_c1b2c3d4e5f60205]
---

# Helios spring launch retrospective

## Compiled Truth

### Key Facts
- The spring launch shipped two weeks late: planned 20 February, actual 6 March 2026.
- The slip was entirely the Nordvolt battery-management firmware bug; the flight software was ready on time.
- The bench fleet caught zero issues because firmware soaks were not required at the time.
- Retro outcomes: 48-hour bench-fleet soak for all firmware, vendor firmware pinning, and a launch-readiness checklist.

### Relationships
- [[bjorn-eriksen]] — ran the retro.
- [[nordvolt]] — vendor whose firmware caused the slip.

### Assessment
Process gap, not an engineering gap. The soak rule was adopted into the
release process within a week, which is unusually fast for a retro action.

## Timeline

- 2026-02-20: Launch postponed after the firmware bug surfaced. [Source: bjorn, slack-#helios, 2026-02-20]
- 2026-03-06: Launch completed at Skagen. [Source: launch report, drive, 2026-03-06]
- 2026-03-12: Retrospective held; three actions agreed. [Source: retro notes, drive, 2026-03-12]
