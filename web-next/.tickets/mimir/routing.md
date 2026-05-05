# Routing / Ingest (/mimir/routing) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir routing matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/ingest.png` (routing rules section)
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (IngestView — routing rules subsection)
**Web-next source:** `packages/plugin-mimir/src/ui/RoutingPage.tsx`, `packages/plugin-mimir/src/ui/RoutingPage.css`

---

## Summary

Web2 shows routing rules as a read-only display within the IngestView. Web-next has a full CRUD routing page (add, edit, delete rules + test path) which is an improvement over web2. The visual test compares against web2's routing rules display. Key gap: the ingest form (URL fetch, file upload) which web2 places alongside routing is handled separately in web-next (see sources ticket). The main work here is the Tailwind migration and ensuring the rules table visual density matches web2.

---

## Required changes

### 1. Ingest form reference (cross-link)

**Web2 spec**: Web2's IngestView combines: (1) ingest form at the top, (2) routing rules below, (3) source list at the bottom. All in one scrollable view.
**Web-next currently**: Routing and sources are separate pages. The ingest form is missing from both (addressed in the sources ticket).
**What to do:** Add a subtle link/note at the top of the routing page pointing to the sources page for ingestion. Something like "To ingest new sources, go to Sources." This bridges the UX gap without combining pages. Alternatively, add a small ingest shortcut (URL input + Fetch button) as a collapsed section at the top — keeping the full form on the Sources page.
**Files to modify:** `packages/plugin-mimir/src/ui/RoutingPage.tsx`

### 2. Migrate RoutingPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Uses `RoutingPage.css` with BEM classes (`.routing-page`, `.routing-page__title`, `.routing-page__table`, `.routing-page__form`, `.routing-page__btn`, `.routing-page__test-pane`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `RoutingPage.css`. Key patterns to migrate:

- Table: standard table with header row, data rows, hover state
- Form: vertical label/input pairs with action buttons
- Test pane: bordered section with input + result display
- Buttons: primary (cyan) and danger (red) variants
  **Files to modify:** `packages/plugin-mimir/src/ui/RoutingPage.tsx`, `packages/plugin-mimir/src/ui/RoutingPage.css` (delete)

### 3. Rules table visual density

**Web2 spec**: Web2's routing rules are rendered as a compact list with: priority number, prefix path (monospace), mount name (chip), active status (colored dot), and description. Rows are tight (padding ~8-10px vertical).
**Web-next currently**: Uses a full HTML table with 6 columns (Priority, Prefix, Mount, Active, Description, Actions). The table is functionally richer (has edit/delete actions) but may be visually denser or sparser than web2.
**What to do:** Verify row padding and font sizes match web2's compact style. Ensure the priority column is narrow (40-50px), prefix is mono-styled, mount uses a chip, active status shows a colored indicator (green dot/text for yes, muted for no), and description is muted text. Keep the Actions column (web-next improvement).
**Files to modify:** `packages/plugin-mimir/src/ui/RoutingPage.tsx`

---

## What to keep as-is

- Full CRUD for routing rules (Add, Edit, Delete) — this is an improvement over web2's read-only display
- Rule form with all fields (prefix, mount, priority, description, active toggle)
- Test path pane with live result display
- Loading and error states
- Empty state message
- `Chip` usage for mount names in table
- Active/inactive visual indicator
- Toolbar with "+ Add rule" button

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`

## Acceptance criteria

1. Routing page includes a reference/link to Sources for ingest functionality
2. Rules table row density matches web2 (compact padding, correct column proportions)
3. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
4. `RoutingPage.css` is deleted
5. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/ingest.png`
6. CRUD operations (add, edit, delete) remain fully functional
7. Test path pane works correctly
8. Form validation and save states render properly
