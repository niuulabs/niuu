---
type: directive
confidence: high
related_entities: []
source_ids: [src_b1b2c3d4e5f60107]
---

# Python style rules

## Compiled Truth

### Key Facts
- Target Python 3.12; use modern union syntax and match statements where they clarify.
- Early returns over nested conditionals; no single-line else after a return.
- Ruff enforces lint and formatting in CI; a build with warnings is a failed build.
- No hardcoded timing values or thresholds in business logic — everything tunable lives in config with defaults.

### Relationships
- (none)

### Assessment
The early-return rule is the one most often flagged in review; everything
else is automated by ruff and rarely comes up.

## Timeline

- 2025-02-14: Style rules adopted with the ruff migration. [Source: rfc-003, wiki, 2025-02-14]
- 2025-10-01: No-magic-numbers rule added after a hardcoded retry count caused a thundering herd. [Source: postmortem, wiki, 2025-10-01]
