# Overview — Visual Parity with web2

**Visual test:** `e2e/visual/ravn.visual.spec.ts` → `ravn overview matches web2`
**Status:** FAIL (grid proportions differ, missing activity log, missing persona avatars, location bar color mismatch)
**Web2 baseline:** `e2e/__screenshots__/web2/ravn/overview.png`
**Web2 source:** `web2/niuu_handoff/ravn/design/overview.jsx`
**Web-next source:** `packages/plugin-ravn/src/ui/OverviewPage.tsx`, `packages/plugin-ravn/src/ui/OverviewPage.css`

---

## Summary

The Overview page has the same conceptual structure as web2 (4 KPIs, active ravens list, location bars, fleet sparkline, top spenders) but differs in grid proportions, is missing the recent activity log tail, and lacks persona avatars in the active ravens list. The location bar fill color uses the wrong token.

---

## Required changes

### 1. Fix grid proportions from 3fr/2fr to 1fr/1fr

**Web2 spec** (overview.jsx): Two-column grid using `grid-template-columns: 1fr 1fr` — equal-width columns.
**Web-next currently**: Uses `grid-template-columns: 3fr 2fr` making the left column 60% and right 40%.
**What to do:** Change `.rv-overview__grid` to use `grid-template-columns: 1fr 1fr`.
**Files to modify:**

- `packages/plugin-ravn/src/ui/OverviewPage.css`

### 2. Add recent activity log (9 rows)

**Web2 spec** (overview.jsx): Right column includes a "Recent activity" section below the top spenders, showing 9 rows with columns: timestamp, kind badge (session/trigger/emit), ravn ID (truncated), and message text.
**Web-next currently**: No activity log section exists. The right column ends after the top spenders list.
**What to do:**

1. Add a `useActivityLog()` hook (or derive from sessions/messages) that returns recent events across the fleet.
2. Add an `ActivityLog` section component below the spenders list rendering a table of 9 rows.
3. Each row: `<timestamp> <kind-badge> <ravn-id-short> <message-text>`.
4. Style with `.rv-activity-log` classes matching web2 spacing (compact rows, monospace timestamps, colored kind badges).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/OverviewPage.tsx`
- `packages/plugin-ravn/src/ui/OverviewPage.css`
- `packages/plugin-ravn/src/ui/hooks/` (new or extended hook for activity data)

### 3. Add persona avatars to active ravens list

**Web2 spec** (overview.jsx): Each row in the active ravens list shows a small persona avatar (colored circle with letter) to the left of the StateDot.
**Web-next currently**: Rows show only `StateDot` + personaName + model. No avatar.
**What to do:**

1. Import `PersonaAvatar` from `@niuulabs/ui`.
2. Add `<PersonaAvatar role={r.role} letter={r.letter} size={16} />` before the StateDot in each `rv-active-row`.
3. Requires the `Ravn` domain model to expose `role` and `letter` fields (check if already available or needs extending).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/OverviewPage.tsx`
- `packages/plugin-ravn/src/domain/ravn.ts` (if `role`/`letter` not yet on model)

### 4. Fix location bar fill color

**Web2 spec** (overview.jsx, tokens.css): Location bars use `var(--brand-300)` for the fill color.
**Web-next currently**: Uses `accent-cyan` (mapped to `var(--color-accent-cyan)`).
**What to do:** Change `.rv-loc-bar-fill` background-color from the cyan accent to `var(--brand-300)`.
**Files to modify:**

- `packages/plugin-ravn/src/ui/OverviewPage.css`

---

## Shared components

- `KpiStrip`, `KpiCard`, `BudgetBar`, `Sparkline`, `StateDot` — from `@niuulabs/ui` (already used)
- `PersonaAvatar` — from `@niuulabs/ui` (needs import in OverviewPage)
- Activity log section — plugin-local (only used by ravn overview)

## Acceptance criteria

1. Grid uses equal 1fr/1fr columns matching web2 baseline screenshot.
2. Active ravens list shows persona avatars (colored circle + letter) before each row.
3. "Recent activity" section appears below top spenders with exactly 9 rows showing timestamp, kind badge, ravn ID, and message.
4. Location bar fill uses `var(--brand-300)` matching web2.
5. Visual regression test `ravn overview matches web2` passes within acceptable diff threshold.
6. Unit tests cover the new activity log section (loading, empty, populated states).
7. Coverage remains at or above 85%.
