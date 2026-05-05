# Overview (/mimir) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir overview matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/home.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/home.jsx`
**Web-next source:** `packages/plugin-mimir/src/ui/OverviewView.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## Summary

The Overview page layout (2-col grid 1.2fr|1fr, KPI strip) matches web2. Mount cards and activity feed are structurally correct. Key gaps are: mount detail expanded view (clicking a mount card in web2 shows inline expanded detail), ravn bio text in warden cards, and the pages-touched metric in ravn cards. The loading/error states in web-next are improvements to keep.

---

## Required changes

### 1. Mount detail expanded view

**Web2 spec**: Clicking a mount card expands it inline to show full configuration details (host, role, categories, per-mount event log excerpt, storage size breakdown).
**Web-next currently**: Mount cards are static; clicking them does nothing (there is a `cursor: pointer` but no click handler).
**What to do:** Add an expandable detail panel to mount cards. On click, expand the card to show host detail, role description, category list with count badges, and a 5-item recent activity excerpt filtered to that mount. Use a disclosure pattern (`aria-expanded`).
**Files to modify:** `packages/plugin-mimir/src/ui/OverviewView.tsx`

### 2. Ravn bio text in warden cards

**Web2 spec**: Each warden card in the overview shows a 1-2 line `bio` field beneath the name/role row (e.g. "Synthesises infrastructure documentation from git commits and runbooks").
**Web-next currently**: Warden cards in the overview show identity (name, rune, role, state dot) and mount bindings, but no bio text.
**What to do:** Add a bio line to the overview ravn card, sourced from the `RavnBinding` domain model. If the model lacks a `bio` field, extend it. Display as a single-line truncated paragraph below the identity row.
**Files to modify:** `packages/plugin-mimir/src/ui/OverviewView.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 3. Pages-touched metric in ravn cards

**Web2 spec**: Each warden card shows `<N> pages touched` and `last dream <time>` in a metrics row at the bottom.
**Web-next currently**: Overview ravn cards do not show a pages-touched count.
**What to do:** Add a metrics row at the bottom of each overview ravn card showing pages-touched count and last-dream timestamp. Source from `RavnBinding` (extend model if needed).
**Files to modify:** `packages/plugin-mimir/src/ui/OverviewView.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 4. Migrate mimir-views.css overview section to Tailwind

**Web2 spec**: N/A — this is a code-quality requirement.
**Web-next currently**: Overview uses BEM classes in `mimir-views.css` (e.g. `.mm-overview`, `.mm-home-cols`, `.mm-mount-card`).
**What to do:** Replace CSS class usage in `OverviewView.tsx` with Tailwind utility classes using the `niuu-` prefix. Remove corresponding rules from `mimir-views.css` once no other component references them. Use token-backed utilities (`niuu-bg-bg-secondary`, `niuu-border-border-subtle`, etc.) — never raw hex values.
**Files to modify:** `packages/plugin-mimir/src/ui/OverviewView.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## What to keep as-is

- KPI strip structure and metrics (already matches web2)
- 2-column grid proportions (1.2fr | 1fr with border-right separator)
- Activity feed layout (time / kind / mount / message grid)
- Loading spinner with `StateDot` + "loading..." text
- Error banner rendering
- `MountChip` component styling and behavior

## Shared components

- `KpiStrip`, `KpiCard` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`
- `RavnAvatar` from `@niuulabs/ui`
- `MountChip` from local `./components/MountChip`

## Acceptance criteria

1. Clicking a mount card expands it to show detail (host, categories, recent activity) with correct `aria-expanded` state
2. Warden cards in overview show a bio line and pages-touched metric matching web2 layout
3. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
4. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/home.png`
5. Loading and error states remain functional
6. No regressions in KPI strip values or activity feed rendering
