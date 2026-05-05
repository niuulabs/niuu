# Pages Reader (/mimir/pages — selected page) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir pages — reader matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/pages-tree.png` (same route, page selected state)
**Web2 source:** `web2/niuu_handoff/mimir/design/pages.jsx`
**Web-next source:** `packages/plugin-mimir/src/ui/PagesView.tsx`, `packages/plugin-mimir/src/ui/components/ZoneBlock.tsx`, `packages/plugin-mimir/src/ui/components/MetaPanel.tsx`

---

## Summary

The pages reader shares the same route as pages-tree but captures the state where a page is selected and its content is rendered. Same structural gaps as pages-tree apply here, plus the missing timeline zone rendered as an append-only list. The zone editing flow is a web-next improvement to keep.

---

## Required changes

### 1. Timeline zone as append-only list

**Web2 spec**: Pages of type `timeline` or pages containing a `timeline` zone render entries as an append-only chronological list. Each entry shows: date (mono, muted), actor/ravn name, and the note/event text. New entries appear at the top (newest first). The timeline zone has a distinct visual treatment — no edit button, entries are separated by subtle border-bottom, and dates are left-aligned in a fixed-width column.
**Web-next currently**: Timeline entries exist in CSS (`.mm-timeline-entry`, `.mm-timeline-date`, `.mm-timeline-note`) but the zone renderer in `ZoneRenderers.tsx` does not have a dedicated timeline rendering path — it falls through to the generic list/prose renderer.
**What to do:** Add a `TimelineZone` renderer in `ZoneRenderers.tsx` that detects `zone.kind === 'timeline'` and renders entries in a 2-column grid (100px date | 1fr note). Mark as read-only (no edit button). Show an empty state ("No timeline entries yet") when the list is empty. Ensure newest-first ordering.
**Files to modify:** `packages/plugin-mimir/src/ui/components/ZoneRenderers.tsx`, `packages/plugin-mimir/src/ui/components/ZoneBlock.tsx`

### 2. Action bar buttons (same as pages-tree ticket)

**Web2 spec**: Edit, Flag, Promote confidence, Cite buttons below the chip bar.
**Web-next currently**: Missing.
**What to do:** Same implementation as described in pages-tree ticket item 3. This is the same file — when pages-tree ticket is completed, this is automatically resolved.
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`

### 3. Reader layout toggle (same as pages-tree ticket)

**Web2 spec**: Structured vs split toggle.
**Web-next currently**: Missing.
**What to do:** Same as pages-tree ticket item 1.
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`

### 4. Wikilink auto-resolution in page body

**Web2 spec**: Wikilinks in zone content are rendered as clickable pills. Broken links show red strikethrough. Clicking a resolved link navigates to that page (selects it in the tree).
**Web-next currently**: `WikilinkPill` exists and `onNavigate` handler is wired, but resolution detection is manual (prop-based, not automatic).
**What to do:** Automatically check each wikilink target against the full page list. Pass `resolved` prop accordingly. Ensure clicking navigates (already wired via `onNavigate`).
**Files to modify:** `packages/plugin-mimir/src/ui/components/ZoneRenderers.tsx`

### 5. Migrate reader/zone CSS to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Zone and reader styling uses BEM classes from `mimir-views.css`.
**What to do:** Replace zone-related class usage with Tailwind utilities using `niuu-` prefix.
**Files to modify:** `packages/plugin-mimir/src/ui/components/ZoneBlock.tsx`, `packages/plugin-mimir/src/ui/components/ZoneRenderers.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## What to keep as-is

- Zone editing with optimistic locking (edit/save/cancel/error flow)
- MetaPanel layout (provenance, sources, backlinks sections)
- Breadcrumb navigation above page title
- Page title + summary rendering
- Chip bar with type/confidence/mount chips
- ZoneBlock expand/collapse behavior

## Shared components

- `ZoneBlock` from `./components/ZoneBlock`
- `ZoneRenderers` from `./components/ZoneRenderers`
- `MetaPanel` from `./components/MetaPanel`
- `WikilinkPill` from `./components/WikilinkPill`
- `MountChip` from `./components/MountChip`

## Acceptance criteria

1. Timeline zones render as an append-only date/note list (newest first) with no edit button
2. Action bar shows Edit, Flag, Promote confidence, Cite buttons
3. Reader layout toggle works (structured vs split)
4. Wikilinks auto-resolve against the page list; broken links show red strikethrough
5. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
6. Visual test passes with ≤5% pixel diff against baseline
7. Zone edit flow remains functional (no regressions in save/cancel/error states)
