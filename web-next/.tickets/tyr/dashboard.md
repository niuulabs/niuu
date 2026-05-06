# Dashboard — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr dashboard matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/dashboard.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/pages.jsx` (DashboardView), `web2/niuu_handoff/tyr/design/shell.jsx` (Topbar)
**Web-next source:** `packages/plugin-tyr/src/ui/DashboardPage.tsx`, `packages/plugin-tyr/src/ui/DashboardPage.css`

---

## Summary

The Dashboard page is structurally complete — 4-column CSS grid, 4 KPI cards with sparklines, saga stream (4 cards), live flock (RaidMeshCanvas), event feed (6 rows), throughput section (2 sparklines). The layout and content match web2 closely. Two gaps remain: (1) topbar stats (dispatcher on, threshold 0.70, concurrent 3/5) visible in web2's Topbar are not surfaced anywhere on the dashboard; (2) the web2 prototype includes a toast notification system used on export/dispatch actions that has no equivalent in web-next.

---

## Required changes

### 1. Add topbar dispatcher stats

**Web2 spec**: The Topbar component in `shell.jsx` lines 78-83 renders three `topbar-meta` spans: `dispatcher <strong>on</strong>`, `threshold <strong>0.70</strong>`, `concurrent <strong>3/5</strong>`. These are always visible when Tyr is active.

**Web-next currently**: The Shell topbar (in `@niuulabs/shell`) does not render dispatcher stats. The `useDispatcher()` hook is called in DashboardPage to warm the query, but its state is not exposed in the topbar area.

**What to do**: Expose dispatcher status in the shell's topbar slot or as a dashboard-level header row. Preferred approach: add a compact stats bar below the shell topbar (or inside it via a plugin-contributed topbar slot) showing `dispatcher: on | threshold: 0.70 | concurrent: 3/5`. The data comes from `useDispatcherState()`.

**Files to modify:**

- `packages/plugin-tyr/src/ui/DashboardPage.tsx` — add a stats row above the KPI grid
- `packages/plugin-tyr/src/ui/DashboardPage.css` — add `.tyr-dash__topbar-stats` styles
- Possibly `packages/shell/src/Topbar.tsx` — if using a plugin slot system

---

### 2. Add toast notification system

**Web2 spec**: `pages.jsx` uses a `flash(msg)` pattern and renders `{toast && <div className="toast">{toast}</div>}` for contextual notifications on actions like export, dispatch, and saga creation.

**Web-next currently**: No toast/notification component exists. Actions like export in `SagasPage.tsx` execute silently with no user feedback.

**What to do**: Implement a `<Toast>` component (or integrate one from `@niuulabs/ui`) that can be triggered imperatively. Wire it into the Dashboard for actions like "View all" navigations, and ensure it is available for other Tyr pages (Dispatch, Sagas). Pattern: a Zustand store or context-based toast queue rendering in a portal at `position: fixed; bottom: 24px; left: 50%`.

**Files to modify:**

- `packages/ui/src/Toast/Toast.tsx` — new shared component
- `packages/ui/src/Toast/Toast.test.tsx` — tests
- `packages/ui/src/index.ts` — export
- `packages/plugin-tyr/src/ui/DashboardPage.tsx` — consume toast for relevant actions

---

### 3. Event feed row "open saga" link button

**Web2 spec**: Each event feed row (`raid-feed-row`) includes a small `↗` ghost button on the far right that navigates to the related saga when clicked. Disabled rows show a `disabled-link` style when no saga matches.

**Web-next currently**: The event feed rows in DashboardPage render 4 columns (StateDot, time, body, subject) but no clickable link/button to open the related saga.

**What to do**: Add a 5th column to `tyr-feed-row` — a ghost button `↗` that calls `navigate({ to: '/tyr/sagas/$sagaId' })` for the matched saga. When no saga matches the subject prefix, render the button as disabled.

**Files to modify:**

- `packages/plugin-tyr/src/ui/DashboardPage.tsx` — add link button to FEED rows
- `packages/plugin-tyr/src/ui/DashboardPage.css` — update `.tyr-feed-row` grid to 5 columns

---

### 4. Raid mesh hover tooltips

**Web2 spec**: The canvas-based raid mesh in web2 renders tooltip overlays (`mesh-tip`) showing raven persona name + raid identifier on hover over raven nodes, and raid name + identifier + raven count + confidence on hover over cluster nodes.

**Web-next currently**: `RaidMeshCanvas.tsx` renders the canvas and supports `onClickSaga` but does not implement hover tooltips.

**What to do**: Add a `hover` state to RaidMeshCanvas that tracks mouse position relative to nodes. Render a positioned tooltip div (absolute within the `.tyr-flock-viz` container) when hovering over cluster or raven nodes, showing the same metadata as web2.

**Files to modify:**

- `packages/plugin-tyr/src/ui/RaidMeshCanvas.tsx` — add hover detection + tooltip rendering
- `packages/plugin-tyr/src/ui/DashboardPage.css` — add `.mesh-tip` styles

---

## Shared components

- `Sparkline` — already in `@niuulabs/ui`, used correctly
- `Pipe` — already in `@niuulabs/ui`, used correctly
- `StateDot` — already in `@niuulabs/ui`, used correctly
- `Toast` — to be added to `@niuulabs/ui` (shared across plugins)

## Acceptance criteria

- [ ] Topbar stats row visible below shell topbar showing dispatcher state, threshold, concurrent count
- [ ] Toast notifications appear on user actions (export, dispatch)
- [ ] Event feed rows include a clickable link button that navigates to the related saga
- [ ] Raid mesh canvas shows hover tooltips with persona/raid metadata
- [ ] `pnpm test` passes with 85%+ coverage on modified files
- [ ] Visual regression test `tyr dashboard matches web2` passes within acceptable pixel diff threshold
- [ ] No inline styles introduced (existing ones in DashboardPage.tsx may remain for now but new code uses Tailwind/CSS classes)
