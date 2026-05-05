# Lint (/mimir/lint) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir lint matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/lint.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (LintView section)
**Web-next source:** `packages/plugin-mimir/src/ui/LintPage.tsx`, `packages/plugin-mimir/src/ui/LintPage.css`

---

## Summary

The lint page layout (KPI strip + 2-column sidebar + issues) matches web2. The sidebar shows per-rule rows with severity dots, IDs, names, and counts. The issues panel shows filtered issues with auto-fix buttons. Key gap: missing rule description box (web2 shows a description panel when a specific rule is selected). LintPage.css needs migration to Tailwind.

---

## Required changes

### 1. Rule description box

**Web2 spec**: When a specific lint rule is selected in the sidebar, a description box appears at the top of the issues panel showing: rule ID, full rule name, severity badge, a 2-3 sentence explanation of what the rule checks, and suggested resolution steps.
**Web-next currently**: When a rule is selected, the issues header shows `{rule} — {description}` as a label, but there is no expanded description box with resolution guidance.
**What to do:** Add a collapsible description box between the issues header and the issue list when a specific rule is selected. Show: rule ID (mono), full description from `RULE_DESCRIPTIONS`, severity (as a colored badge), and a brief "How to fix" hint. The descriptions can be extended in the `RULE_DESCRIPTIONS` constant or a new `RULE_DETAILS` map.
**Files to modify:** `packages/plugin-mimir/src/ui/LintPage.tsx`

### 2. Migrate LintPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Uses `LintPage.css` with BEM classes (`.lint-page`, `.lint-page__kpi-strip`, `.lint-page__sidebar`, `.lint-page__check-row`, `.lint-page__issues-panel`, `.lint-page__issue`, `.lint-page__btn`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `LintPage.css`. Key patterns to migrate:

- KPI strip: horizontal flex/grid with individual KPI cards
- 2-column wrap: `grid-template-columns: 220px 1fr`
- Sidebar check rows: vertical list with active state background
- Issue list items: flex rows with severity-based left border/dot
- Buttons: match the shared button pattern from other pages
  **Files to modify:** `packages/plugin-mimir/src/ui/LintPage.tsx`, `packages/plugin-mimir/src/ui/LintPage.css` (delete)

### 3. KPI strip alignment with web2

**Web2 spec**: Web2's lint KPI strip uses 4 columns: total issues (amber accent), errors (red when > 0), warnings (amber), auto-fixable (cyan accent). Values are large (22-24px mono), labels are small uppercase.
**Web-next currently**: KPI strip has 4 items but the visual weight and color coding may not precisely match. The `lint-page__kpi-val--err` and `lint-page__kpi-val--warn` variants exist but need to be verified against the baseline.
**What to do:** Ensure KPI value sizes match web2 (large mono text), accent colors match (amber for total, red for errors, amber for warnings, cyan for auto-fixable), and labels are uppercase with letter-spacing.
**Files to modify:** `packages/plugin-mimir/src/ui/LintPage.tsx`

---

## What to keep as-is

- 2-column layout (220px sidebar + 1fr issues panel)
- Sidebar "All" row + per-rule rows with severity dot, ID, name, count
- Active state highlighting in sidebar
- Issue list with severity-colored indicators
- Auto-fix buttons (per-issue and bulk "Fix all")
- Run lint button
- `LintBadge` component
- Loading and error states
- Empty state messages (per-rule and global)
- `StateDot` severity mapping

## Shared components

- `StateDot` from `@niuulabs/ui`
- `LintBadge` from `./LintBadge`

## Acceptance criteria

1. Selecting a lint rule shows an expanded description box with rule details and fix guidance
2. KPI strip values use correct color coding (amber total, red errors, amber warnings, cyan fixable)
3. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
4. `LintPage.css` is deleted
5. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/lint.png`
6. Auto-fix functionality (single and bulk) remains operational
7. Sidebar filtering works correctly (All vs specific rule)
8. Loading and error states render properly
