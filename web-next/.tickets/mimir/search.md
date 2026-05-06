# Search (/mimir/search) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir search matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/search.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (SearchView, SearchResult)
**Web-next source:** `packages/plugin-mimir/src/ui/SearchPage.tsx`, `packages/plugin-mimir/src/ui/SearchPage.css`

---

## Summary

The search page layout (heading + controls + results list) matches web2. Mode toggle buttons and highlight utility are correct. Key gaps are: score display per result (web2 shows "score 0.82" aligned right in the header), and mount chips in results (web2 shows which mount(s) each result lives in). Category and confidence chips in web-next are extras to keep.

---

## Required changes

### 1. Score display per result

**Web2 spec**: Each search result shows a score value (e.g. "score 0.82") right-aligned on the same row as the title. The score is rendered in monospace, muted text color, font-size 10-11px.
**Web-next currently**: Results show title, category chip, confidence chip, summary, and path — but no score value.
**What to do:** Add a score display to the result header row, right-aligned. Show "score {value}" in mono font, muted color. The score should come from the search result domain model (extend `SearchResult` type if needed to include a `score: number` field from the service response).
**Files to modify:** `packages/plugin-mimir/src/ui/SearchPage.tsx`, `packages/plugin-mimir/src/domain/api-types.ts` (if SearchResult lacks score field)

### 2. Mount chips in results

**Web2 spec**: Each search result shows mount chips (e.g. "local", "shared") at the end of the chips row, indicating which mount(s) the page lives in.
**Web-next currently**: Results show category and confidence chips but no mount chips.
**What to do:** Add mount chips to each result row after the confidence chip. Each mount name gets its own `Chip` (or `MountChip` component). Data comes from the search result's `mounts` field (ensure the domain model includes it).
**Files to modify:** `packages/plugin-mimir/src/ui/SearchPage.tsx`, `packages/plugin-mimir/src/ui/components/MountChip.tsx`

### 3. Migrate SearchPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Search page uses a dedicated `SearchPage.css` file with BEM classes (`.search-page`, `.search-page__input`, `.search-page__mode-btn`, `.search-page__result`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `SearchPage.css` entirely once migration is complete. Ensure the highlight `<mark>` styling uses Tailwind (e.g. `niuu-bg-accent-amber/20 niuu-text-text-primary`).
**Files to modify:** `packages/plugin-mimir/src/ui/SearchPage.tsx`, `packages/plugin-mimir/src/ui/SearchPage.css` (delete)

---

## What to keep as-is

- Search input with placeholder text
- Mode toggle buttons (Full-text / Semantic / Hybrid) with active state
- Result highlighting (bold matched terms in title and summary)
- Category chip per result
- Confidence chip per result (tone varies by level)
- Empty state messages ("No results found for...")
- Loading state with `StateDot` + "searching..."
- Error state rendering
- Result path display

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`
- `MountChip` from `./components/MountChip` (to be added to results)

## Acceptance criteria

1. Each search result displays a numeric score right-aligned in the header row
2. Each search result displays mount chips showing which mount(s) the page belongs to
3. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
4. `SearchPage.css` is deleted; all styling is inline Tailwind
5. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/search.png`
6. Search functionality (query, mode switching, highlighting) remains intact
7. Loading, error, and empty states render correctly
