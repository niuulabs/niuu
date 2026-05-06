# Dreams / Log (/mimir/dreams) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir dreams matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/log.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (LogView section)
**Web-next source:** `packages/plugin-mimir/src/ui/DreamsPage.tsx`, `packages/plugin-mimir/src/ui/DreamsPage.css`

---

## Summary

Web2 has a `LogView` that shows an activity event log (time, kind, mount, ravn, message — flat event rows). Web-next has a `DreamsPage` that shows dream-cycle history (grouped synthesis passes with aggregate stats). These are conceptually different views — web2 shows granular events, web-next shows higher-level dream cycles. The visual test compares against web2's log screenshot, so the DreamsPage needs to incorporate the event-log style from web2 while keeping its dream-cycle grouping as additional context.

---

## Required changes

### 1. Activity event log section

**Web2 spec**: LogView shows a chronological list of activity events. Each row is a 4-column grid: timestamp (mono, 50px), event kind (colored by type — write/ingest/lint/dream/query), mount name, and message (truncated). Events are newest-first. The log can be filtered by kind.
**Web-next currently**: DreamsPage shows dream cycles as grouped cards, each with timestamp, ravn chip, duration, mount chips, and aggregate stats (pages updated, entities created, lint fixes). No granular event log.
**What to do:** Add an "Activity log" section below the dream cycles list (or as a togglable tab). Show recent activity events in the same grid format as web2: time | kind (colored) | mount | message. Source from a service method (e.g. `IMimirService.getActivityLog()`). If the method doesn't exist, add it to the port interface and mock adapter. Include kind filter buttons (all / write / ingest / lint / dream).
**Files to modify:** `packages/plugin-mimir/src/ui/DreamsPage.tsx`, `packages/plugin-mimir/src/ports/index.ts`, `packages/plugin-mimir/src/application/useDreams.ts`

### 2. Visual density matching web2 log rows

**Web2 spec**: Event rows are very compact — 28-32px row height, 10-11px mono font, tight padding. The overall feel is a terminal-like log output.
**Web-next currently**: Dream cycle cards have generous padding (var(--space-4)) and are visually spacious.
**What to do:** Ensure the activity log section uses compact row styling: tight padding (6-8px vertical), mono font at 11px, grid columns matching web2 (50px time | 60px kind | 66px mount | 1fr message). Keep dream cycle cards spacious (they represent different data).
**Files to modify:** `packages/plugin-mimir/src/ui/DreamsPage.tsx`

### 3. Migrate DreamsPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Uses `DreamsPage.css` with BEM classes (`.dreams-page`, `.dreams-page__title`, `.dreams-page__cycle`, `.dreams-page__cycle-header`, `.dreams-page__cycle-stats`, `.dreams-page__cycle-stat`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `DreamsPage.css`. Key patterns:

- Page title and subtitle: standard heading + muted paragraph
- Cycle list: vertical list with border-separated items
- Cycle header: flex row with time, ravn chip, duration
- Cycle stats: inline metrics with conditional "active" highlighting
- Activity log rows (new): tight grid with colored kind column
  **Files to modify:** `packages/plugin-mimir/src/ui/DreamsPage.tsx`, `packages/plugin-mimir/src/ui/DreamsPage.css` (delete)

### 4. Kind filter for activity log

**Web2 spec**: Web2's log has filter chips at the top: all / write / ingest / lint / dream / query. Clicking a filter narrows the displayed events.
**Web-next currently**: No filtering on the dreams page (dream cycles are shown unfiltered).
**What to do:** Add a filter bar above the activity log section with kind filter buttons (matching web2's set). Active filter uses brand/cyan styling, inactive uses muted. Filter is client-side on the fetched events array.
**Files to modify:** `packages/plugin-mimir/src/ui/DreamsPage.tsx`

---

## What to keep as-is

- Dream cycle cards (grouped synthesis pass view) — this is a web-next improvement
- Per-cycle stats (pages updated, entities created, lint fixes)
- Ravn chip per cycle
- Mount chips per cycle
- Duration display
- Loading state with `StateDot`
- Error state rendering
- Empty state message
- `Chip` component usage
- `formatDuration` and `formatTimestamp` utilities

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`

## Acceptance criteria

1. An activity log section displays granular events in a compact grid (time | kind | mount | message)
2. Kind filter buttons allow narrowing the log by event type
3. Activity log rows are visually compact (~30px height, mono font, colored kind labels)
4. Dream cycle cards remain above/alongside the activity log
5. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
6. `DreamsPage.css` is deleted
7. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/log.png`
8. Loading, error, and empty states render correctly
9. Event kind colors match established palette (write=cyan, ingest=indigo, lint=emerald, dream=purple)
