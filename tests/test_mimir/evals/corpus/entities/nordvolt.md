---
type: entity
confidence: medium
entity_type: organization
related_entities: [asgard-robotics]
source_ids: [src_a1b2c3d4e5f60007]
---

# Nordvolt

## Compiled Truth

### Key Facts
- Scandinavian battery manufacturer supplying flight batteries for the Helios drone fleet.
- Selected over two competitors in the 2026 vendor review on energy density and cold-weather performance.
- Ships lithium-polymer packs with a 420 Wh/kg energy density rating.
- A firmware bug in their battery management system caused the two-week Helios launch slip.

### Relationships
- [[asgard-robotics]] — supplies flight batteries for the drone fleet.

### Assessment
Best cells available for cold-climate flight, but their firmware quality is a
known risk. Keep the battery management firmware version pinned and test
upgrades on the bench fleet first.

## Timeline

- 2026-01-28: Won the battery vendor selection. [Source: vendor review, drive, 2026-01-28]
- 2026-02-20: Battery management firmware bug found during launch prep. [Source: bjorn, slack-#helios, 2026-02-20]
- 2026-03-05: Fixed firmware shipped and validated on the bench fleet. [Source: vendor email, mail, 2026-03-05]
