---
type: research
confidence: high
produced_by_thread: true
related_entities: []
source_ids: [src_4f708cf8a2eee67a]
---

# Best Practices for Agent-Written Mímir Pages

> **TL;DR** — Agent personas should follow a consistent template, cite sources without copying text, search before writing to avoid duplicates, and pass a short lint checklist before committing a page.

## Compiled Truth

### Page Front Matter Template

Every agent-written Mímir research page must begin with YAML front matter containing these fields:

- `type` — Required. Always set to `research` for agent-produced pages.
- `confidence` — Required. One of `low`, `medium`, or `high` — the agent's own epistemic assessment.
- `produced_by_thread` — Required. Always `true` for agent-authored pages, marking machine origin.
- `related_entities` — Optional. A list of slugs for cross-linked pages.
- `source_ids` — Optional. Identifiers for external sources consulted during synthesis.

This convention distinguishes agent-authored content from human-authored reference material and enables automated tooling to identify provenance.

### Body Structure

Agent pages should follow this body ordering:

1. **TL;DR** — A one-line summary near the top for fast skimming by other agents and humans.
2. **`## Compiled Truth`** — The main synthesised content. Prefer bullet lists and tables over prose. Use prose only when nuance requires it.
3. **`## Timeline`** *(optional)* — Dated evidence entries, newest first. Include only when recency matters (e.g., version changes, incident history).

Target **under 1 500 words**. If a topic expands beyond that, split into focused child pages and cross-link rather than writing a single sprawling article.

### Source Attribution

- **Restate, don't reproduce.** Paraphrase findings from source material; never paste source text verbatim.
- **Cite inline.** Reference the source name or URL in parentheses at the end of the relevant bullet, e.g. `(MDN Web Docs, 2024)`.
- **List sources explicitly.** Use the `source_ids` front matter field. When more than three sources are used, also add a `## Sources` appendix at the bottom.
- **Note access dates.** For web sources that may change over time, record the retrieval date, e.g. `(retrieved 2026-05-03)`.
- **Prefer primary sources.** Official documentation, RFC specifications, and original papers outrank secondary commentary.

### Duplicate Avoidance

1. **Search first.** Call `mimir_search` with the core topic before writing. If a relevant page already exists, extend it rather than creating a new one — unless the new angle is materially distinct in scope or audience.
2. **Use narrow slugs.** Prefer specific paths like `research/openai-rate-limit-strategies.md` over broad ones like `research/apis.md`. Narrower scope reduces collisions.
3. **Cross-link, don't repeat.** When an adjacent page already covers a sub-topic, link to it and omit the repeated content.
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
- [ ] Related pages are cross-linked where relevant
- [ ] `## Timeline` section included only when dated evidence is materially useful

### Preferred Content Formats

| Content type | Preferred format |
|---|---|
| Discrete facts / findings | Bullet list |
| Comparisons / trade-offs | Table |
| Nuanced explanation | Short prose paragraph |
| Dated evidence / history | `## Timeline` entries |
| Long-form background | Separate child page, cross-linked |

## Timeline

- 2026-05-03: Initial guidance authored from NIU-776 task description and Mímir system prompt conventions.
- 2026-05-04: Source synthesised and consolidated into canonical page; prior duplicates replaced with redirect notices.

## Sources

- NIU-776 task description (internal, 2026-05-03)
- Mimir system prompt conventions (agent harness, 2026-05-03)

<!-- sources: src_4f708cf8a2eee67a -->
