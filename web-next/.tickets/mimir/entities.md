# Entities (/mimir/entities) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir entities matches web2`
**Status:** PASS (self-referential baseline)
**Web2 baseline:** N/A — new page not in web2 prototype
**Web2 source:** N/A
**Web-next source:** `packages/plugin-mimir/src/ui/EntitiesPage.tsx`, `packages/plugin-mimir/src/ui/EntitiesPage.css`

---

## Summary

The Entities page is a NEW page in web-next that does not exist in web2. There is no web2 baseline to compare against — it is its own reference. The current implementation shows a kind-based filter bar, grouped entity lists, and individual entity cards with title, relationship count, summary, and path. The visual test uses the web-next screenshot as its own baseline. The only work needed is the Tailwind migration for code quality.

---

## Required changes

### 1. Migrate EntitiesPage.css to Tailwind

**Web2 spec**: N/A — this page has no web2 equivalent.
**Web-next currently**: Uses `EntitiesPage.css` with BEM classes (`.entities-page`, `.entities-page__title`, `.entities-page__filter`, `.entities-page__filter-btn`, `.entities-page__group`, `.entities-page__item`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `EntitiesPage.css` once migration is complete. Ensure filter buttons active state uses token-backed colors (brand/cyan for active, bg-secondary for inactive).
**Files to modify:** `packages/plugin-mimir/src/ui/EntitiesPage.tsx`, `packages/plugin-mimir/src/ui/EntitiesPage.css` (delete)

### 2. Visual consistency with other Mimir pages

**Web2 spec**: N/A.
**Web-next currently**: Uses the same general visual language (heading, subtitle, filter bar, card list) but the specific spacing and typography should match the patterns established in other Mimir pages.
**What to do:** Ensure the page title uses the same heading style as other pages (e.g. `niuu-text-xl niuu-font-semibold niuu-text-text-primary`). Ensure filter buttons match the mode toggle pattern used in SearchPage. Ensure entity list items have the same card border/padding/radius as lint issue rows and search result rows.
**Files to modify:** `packages/plugin-mimir/src/ui/EntitiesPage.tsx`

---

## What to keep as-is

- Entity kind filter bar (All + per-kind buttons with emoji icons)
- Grouped display by entity kind with section headings
- Individual entity cards (title, relationship count chip, summary, path)
- Loading state with `StateDot`
- Error state rendering
- Empty state message
- `Chip` usage for relationship counts and kind group counts

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`

## Acceptance criteria

1. `EntitiesPage.css` is deleted; all styling uses Tailwind with `niuu-` prefix
2. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
3. Page heading, filter bar, and card styles are visually consistent with other Mimir sub-pages
4. Visual test passes with ≤5% pixel diff (baseline is self-referential)
5. Entity filtering, grouping, loading, and error states remain functional
