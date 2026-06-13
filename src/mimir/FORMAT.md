# Mímir Page Format Specification

Version 1.0 — 2026-04-12

## Overview

Mímir pages use a **compiled truth + timeline** two-zone format that separates
synthesis ("what we know") from evidence ("how we know it"). This separation is
the intellectual core of the compounding-knowledge system: the compiled-truth
zone is rewritten as understanding evolves, while the timeline zone is
append-only and never edited.

---

## Frontmatter

Every page MUST begin with a YAML frontmatter block delimited by `---`.

```yaml
---
type: directive | decision | goal | preference | observation | entity | topic
confidence: high | medium | low
entity_type: person | project | concept | technology | organization | strategy  # required when type=entity
related_entities: [slug-1, slug-2]   # wikilink slugs for related pages
source_ids: [src_abc123]             # raw source IDs that back this page
---
```

### Field reference

| Field | Required | Values | Notes |
|-------|----------|--------|-------|
| `type` | Yes | `directive`, `decision`, `goal`, `preference`, `observation`, `entity`, `topic` | Controls which body sections are required |
| `confidence` | No | `high`, `medium`, `low` | Epistemic confidence in the compiled truth |
| `entity_type` | Conditional | `person`, `project`, `concept`, `technology`, `organization`, `strategy` | Required when `type: entity` |
| `related_entities` | No | list of slugs | Slugs map to `wiki/entities/<slug>.md` |
| `source_ids` | No | list of source IDs | References to raw ingested sources |

---

## Body Zones

Pages of type `entity`, `directive`, or `decision` MUST contain both zones.
Other types (goal, preference, observation, topic) SHOULD include both zones
but validators emit warnings, not errors, for their absence.

### Zone 1 — Compiled Truth

```markdown
## Compiled Truth

### Key Facts
- Bullet-point facts that are currently believed to be true.

### Relationships
- [[entity-slug]] — brief description of the relationship.
- [[entity-slug]] — rel: works_at — typed relationship with a label.

### Assessment
Free-form synthesis: interpretation, implications, open questions.
```

**Typed relationships (additive, NIU-1058):** a relationship line may carry a
machine-readable label using `— rel: <type> —` between the wikilink and the
description (e.g. `works_at`, `part_of`, `supplies`, `leads`). Typed edges are
surfaced by `GET /mimir/related?rel=<type>` and the graph API; untyped lines
remain fully valid and are treated as unlabeled edges.

**Rules:**

- The `## Compiled Truth` heading is the canonical zone marker (case-sensitive).
- The entire zone may be rewritten by `rewrite_compiled_truth()` as understanding
  matures.
- `[[entity-slug]]` wikilinks reference other Mímir entity pages.  The slug must
  match the filename stem under `wiki/entities/`.

### Zone 2 — Timeline

```markdown
## Timeline

- YYYY-MM-DD: Description of the event or observation. [Source: who, channel, date]
- YYYY-MM-DD: Another entry. [Source: name, slack-#channel, 2026-03-15]
```

**Rules:**

- The `## Timeline` heading is the canonical zone marker (case-sensitive).
- Entries are **append-only** — existing entries MUST NOT be edited, deleted, or
  reordered.
- Each entry MUST match the pattern:
  `- YYYY-MM-DD: <description>. [Source: <attribution>]`
- The `[Source: ...]` attribution is mandatory.  Entries without it fail
  validation.

---

## Wikilink Syntax

Use `[[slug]]` to link from any page to a named entity page.

- `[[person-karpathy]]` resolves to `wiki/entities/person-karpathy.md`
- Slugs are lowercase, hyphen-separated (produced by `slugify()`).
- Unresolved wikilinks (target file does not exist) are flagged by the linter
  but are not hard errors at write time.

---

## Example Page

```markdown
---
type: entity
confidence: high
entity_type: person
related_entities: [project-niuu, concept-compounding-knowledge]
source_ids: [src_abc123, src_def456]
---

# Andrej Karpathy

## Compiled Truth

### Key Facts
- AI researcher, former Tesla AI director.
- Advocates for small, focused language models.

### Relationships
- [[project-niuu]] — referenced as an inspiration for the compounding knowledge design.

### Assessment
High-signal follow; outputs are consistently practical and well-reasoned.

## Timeline

- 2026-03-10: Shared post on X about minimal LLM architectures. [Source: @karpathy, X/Twitter, 2026-03-10]
- 2026-04-01: Published blog post on memory systems. [Source: karpathy.github.io, web, 2026-04-01]
```

---

## Backwards Compatibility

The compiled-truth format is **additive**.  Existing pages without frontmatter
or without the two zones continue to be readable; validators emit structured
`ValidationError` objects but do not raise exceptions.  The `mimir_write` tool
will eventually enforce the format, but legacy pages are not broken by this
specification.
