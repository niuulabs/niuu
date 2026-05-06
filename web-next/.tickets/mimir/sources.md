# Sources (/mimir/sources) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir sources matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/ingest.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (IngestView section)
**Web-next source:** `packages/plugin-mimir/src/ui/SourcesView.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## Summary

Web2 combines ingest (form + source list) into a single `IngestView`. Web-next separates concerns: `SourcesView` shows the source table (matching the list portion of web2) and `RoutingPage` handles routing rules. The structured Table component in web-next is an improvement to keep. Key gap is the missing ingest form UI (fetch URL, upload file inputs) that web2 renders above the source list.

---

## Required changes

### 1. Ingest form UI (fetch URL, upload file)

**Web2 spec**: Above the source list, web2 shows an ingest form with two input modes: (a) "Fetch URL" — a text input for a URL + a "Fetch" button, and (b) "Upload file" — a file drop zone / file picker. Both submit to the ingest pipeline and the new source appears in the list below. A mode toggle switches between URL and file.
**Web-next currently**: `SourcesView` shows only the origin filter tabs and the source table. No ingest form exists.
**What to do:** Add an ingest form section above the origin filter tabs. Include: a mode toggle (URL | File), a URL text input with "Fetch" button for URL mode, a file dropzone/picker for file mode. Wire to the `IMimirService.sources.ingest()` port method (stub if not yet implemented). Show a loading state during ingestion and append the new source to the list on success.
**Files to modify:** `packages/plugin-mimir/src/ui/SourcesView.tsx`, `packages/plugin-mimir/src/ports/index.ts` (if ingest method is missing)

### 2. Source list visual alignment

**Web2 spec**: Each source row in web2 shows: origin badge (colored by type), title (primary text), URL/path (mono, cyan for URLs), ingest date + agent, and compiled-into page chips.
**Web-next currently**: Uses a `Table` component with custom column renderers. The visual output is close but the structured table adds header rows and uniform column widths that differ slightly from web2's more fluid list.
**What to do:** Verify that column widths and row heights visually match the web2 screenshot. The Table component is fine to keep — adjust column `width` props if needed to match the proportional balance in web2. Ensure origin badges have the correct color mapping per type.
**Files to modify:** `packages/plugin-mimir/src/ui/SourcesView.tsx`

### 3. Migrate sources CSS to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Sources view uses BEM classes from `mimir-views.css` (`.mm-sources`, `.mm-origin-tabs`, `.mm-origin-chip`, `.mm-source-*`, `.mm-origin-badge`).
**What to do:** Replace class-based styling with Tailwind utilities using `niuu-` prefix. Remove dead CSS from `mimir-views.css`.
**Files to modify:** `packages/plugin-mimir/src/ui/SourcesView.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## What to keep as-is

- Structured `Table` component usage (improvement over web2's inline rendering)
- Origin filter tabs (all / web / rss / arxiv / file / mail / chat)
- Loading state with `StateDot`
- Error state rendering
- Source count display below filters
- Column renderers (ID/origin badge, title+meta, origin URL/path, compiled-into chips)

## Shared components

- `Table` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`

## Acceptance criteria

1. Ingest form appears above the source list with URL and File mode toggle
2. URL mode shows a text input + Fetch button; File mode shows a dropzone/file picker
3. Successful ingest appends the new source to the list
4. Source list row layout visually matches web2 proportions
5. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
6. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/ingest.png`
7. Origin filter tabs and Table component remain functional
