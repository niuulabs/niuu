---
type: directive
confidence: high
related_entities: [bjorn-eriksen]
source_ids: [src_b1b2c3d4e5f60108]
---

# Release process

## Compiled Truth

### Key Facts
- Releases cut from the dev branch every Tuesday; main is for production tags only.
- Every release requires green CI, a passing canary deploy, and a rollback plan in the release notes.
- Drone firmware releases additionally require a bench-fleet soak of at least 48 hours.
- Hotfixes branch from the production tag, never from dev.

### Relationships
- [[bjorn-eriksen]] — added the bench-fleet soak requirement after the battery firmware slip.

### Assessment
The 48-hour soak rule exists because the Nordvolt firmware bug reached the
launch fleet without bench time. It has caught one regression since.

## Timeline

- 2025-06-03: Weekly release cadence adopted. [Source: rfc-011, wiki, 2025-06-03]
- 2026-02-25: Bench-fleet soak requirement added for firmware. [Source: bjorn, slack-#helios, 2026-02-25]
