---
type: research
confidence: high
produced_by_thread: true
related_entities: [mimir-mount-selection, research-persona-evaluation-rubric]
source_ids: [src_4f708cf8a2eee67a, src_niu776_format_md]
---

# Best Practices for Agent-Written Mímir Pages

> **TL;DR** — Agent personas should follow a consistent template, cite sources without copying text, search before writing to avoid duplicates, and pass a short lint checklist before committing a page.

> **See also:** [Mimir Mount Selection](mimir-mount-selection.md) — which Mimir volumes are mounted per persona. [Research Persona Evaluation Rubric](research-persona-evaluation-rubric.md) — scoring criteria for research page quality.

## Compiled Truth

### Page Template

Copy this skeleton when creating a new research page. Fill in every required field; delete optional sections if unused.

````markdown
---
type: research
confidence: low | medium | high
produced_by_thread: true
related_entities: []          # optional — list of related page slugs
source_ids: []                # optional — identifiers for sources consulted
---

# <Title>

> **TL;DR** — One sentence summary for fast skimming.

> **See also:** [<Related page>](related-slug.md) — <what distinguishes this one>. *(Delete if no overlap.)*

## Compiled Truth

<Main synthesised content. Use bullets and tables; fall back to prose only when nuance requires it.>

## Timeline

*(Delete this section unless dated evidence is materially useful.)*

- YYYY-MM-DD: <Observation or event.> [Source: <name or URL, date>]

## Sources

*(Required when 3+ sources are used.)*

- <Source name> — <one-line description> (<retrieved YYYY-MM-DD>)

<!-- sources: <source_ids comma-separated> -->
````

### Front Matter Fields

Every agent-written Mímir research page must begin with YAML front matter. Fields are defined in `src/mimir/FORMAT.md`; the table below documents the Niuu-specific values for research pages:

| Field | Required | Value for research pages | Notes |
|---|---|---|---|
| `type` | Yes | `research` | Niuu extension — not in the base FORMAT.md type list |
| `confidence` | Yes | `low` \| `medium` \| `high` | Agent's epistemic assessment |
| `produced_by_thread` | Yes | `true` | Niuu extension; marks machine origin for automated tooling |
| `related_entities` | No | list of slugs | Populate whenever related pages exist; enables cross-link tooling |
| `source_ids` | No | list of source IDs | IDs for ingested sources that back this page |

`produced_by_thread` and `type: research` are Niuu-specific extensions to the base format spec. All other fields are standard.

**Source ID naming convention** — two forms are in use; prefer the ticket-scoped form when the source is tied to a specific issue:

| Form | When to use | Example |
|---|---|---|
| `src_<ticket>_<component>` | Source tied to a specific Linear ticket | `src_niu775_composite_adapter` |
| `src_<hash>` | Session-scoped source with no ticket | `src_4f708cf8a2eee67a` |

### Body Structure

Agent pages should follow this body ordering:

1. **TL;DR** — A one-line summary near the top for fast skimming by other agents and humans.
2. **`## Compiled Truth`** — The main synthesised content. Prefer bullet lists and tables over prose. Use prose only when nuance requires it.
3. **`## Timeline`** *(optional)* — Dated evidence entries, newest first. Include only when recency matters (e.g., version changes, incident history).
4. **`## Sources`** *(required when 3+ sources)* — Full source list at the bottom.

Target **under 1 500 words**. If a topic expands beyond that, split into focused child pages and cross-link rather than writing a single sprawling article.

**Zone heading case-sensitivity** — `## Compiled Truth` and `## Timeline` are parsed by the Mímir validator as canonical zone markers. The heading text is case-sensitive; variations like `## compiled truth` or `## TIMELINE` are not recognised.

**Timeline entry format** (required by FORMAT.md — entries without `[Source: ...]` fail validation):
```
- YYYY-MM-DD: Description of event or observation. [Source: name, channel/URL, date]
```

**Timeline entries are append-only.** Never edit, delete, or reorder existing timeline entries. The timeline zone is an immutable evidence log; only the Compiled Truth zone is rewritten as understanding evolves.

### Source Attribution

- **Restate, don't reproduce.** Paraphrase findings from source material; never paste source text verbatim.
- **Cite inline.** Reference the source name or URL in parentheses at the end of the relevant bullet, e.g. `(MDN Web Docs, 2024)`.
- **List sources explicitly.** Use the `source_ids` front matter field. When more than three sources are used, also add a `## Sources` appendix at the bottom.
- **Note access dates.** For web sources that may change over time, record the retrieval date, e.g. `(retrieved 2026-05-03)`.
- **Prefer primary sources.** Official documentation, RFC specifications, and original papers outrank secondary commentary.

### Duplicate Avoidance

1. **Search first.** Call `mimir_search` with the core topic before writing. If a relevant page already exists, extend it rather than creating a new one — unless the new angle is materially distinct in scope or audience.
2. **Use narrow slugs.** Prefer specific paths like `research/openai-rate-limit-strategies.md` over broad ones like `research/apis.md`. Narrower scope reduces collisions.
3. **Cross-link, don't repeat.** When an adjacent page already covers a sub-topic, link to it and omit the repeated content. Use standard markdown links (`[Title](slug.md)`) for other research pages. Use `[[slug]]` wikilink syntax only for entity pages (`wiki/entities/<slug>.md`); list the same slugs in `related_entities` in front matter.
4. **Declare overlap explicitly.** If a new page partially overlaps an existing one, add a `> **See also:**` callout near the top naming the related page and what distinguishes this one.

### Lint and Review Checklist

Run this checklist before committing any agent-written page:

- [ ] `produced_by_thread: true` is present in front matter
- [ ] `type` and `confidence` fields are set
- [ ] Page path matches `research/{slug}.md` naming convention
- [ ] `mimir_search` was called and no duplicate page exists (or overlap is documented)
- [ ] No verbatim copy-paste from source material
- [ ] All claims link to or name a source
- [ ] Body is under 1 500 words
- [ ] TL;DR is present and accurate
- [ ] Related pages are cross-linked in body and listed in `related_entities`
- [ ] `## Timeline` section included only when dated evidence is materially useful
- [ ] All timeline entries include `[Source: ...]` attribution per FORMAT.md
- [ ] No existing timeline entries were edited, deleted, or reordered (append-only)
- [ ] `[[slug]]` wikilinks are used only for entity pages; research page cross-links use markdown links
- [ ] Source IDs follow the naming convention (`src_<ticket>_<component>` preferred)

### Preferred Content Formats

| Content type | Preferred format |
|---|---|
| Discrete facts / findings | Bullet list |
| Comparisons / trade-offs | Table |
| Nuanced explanation | Short prose paragraph |
| Dated evidence / history | `## Timeline` entries |
| Long-form background | Separate child page, cross-linked |

## Timeline

- 2026-05-05: Cross-referenced page against FORMAT.md spec; added append-only rule for timeline zone, clarified that `[[slug]]` wikilinks resolve to entity pages (not research pages), added case-sensitivity note for zone headings, expanded lint checklist with two new items. [Source: src/mimir/FORMAT.md, niuulabs/volundr, 2026-05-05]
- 2026-05-05: Audited page against `src/mimir/FORMAT.md`; found timeline entries lacked required `[Source: ...]` attribution and `related_entities` was unpopulated. Updated front matter table, added source-ID naming convention, added wikilink guidance, expanded lint checklist, fixed timeline entries. [Source: src/mimir/FORMAT.md, niuulabs/volundr, 2026-05-05]
- 2026-05-05: Added concrete copy-paste page template skeleton to make guidance immediately actionable for new agents. [Source: NIU-776, internal, 2026-05-05]
- 2026-05-04: Consolidated four duplicate pages into canonical page; replaced duplicates with redirect notices; updated index. [Source: NIU-776 synthesis pass, internal, 2026-05-04]
- 2026-05-03: Initial guidance authored from NIU-776 task description and Mímir system prompt conventions. [Source: NIU-776 task description, internal, 2026-05-03]

## Sources

- NIU-776 task description (internal, 2026-05-03)
- Mimir system prompt conventions (agent harness, 2026-05-03)
- `src/mimir/FORMAT.md` — authoritative Mímir page format spec (niuulabs/volundr, retrieved 2026-05-05)

<!-- sources: src_4f708cf8a2eee67a, src_niu776_format_md -->
