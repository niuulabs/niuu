# Pages Tree (/mimir/pages) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir pages — tree matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/pages-tree.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/pages.jsx`
**Web-next source:** `packages/plugin-mimir/src/ui/PagesView.tsx`, `packages/plugin-mimir/src/ui/components/TreeNode.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## Summary

The 3-pane grid layout (220px | 1fr | 280px) matches web2. File tree, page reader, and meta panel are structurally correct. Key gaps are: reader layout toggle (structured vs split-pane raw sources), a split-pane raw sources view, action bar buttons (Edit, Flag, Promote confidence, Cite), and wikilink breaking detection. Zone editing with optimistic locking is a web-next improvement to keep.

---

## Required changes

### 1. Reader layout toggle (structured vs split)

**Web2 spec**: A toggle row above the page content lets the user switch between "structured" view (zones rendered) and "split" view (left: rendered zones, right: raw source text side-by-side).
**Web-next currently**: Only the structured view exists; no toggle control.
**What to do:** Add a toggle button group (Structured | Split) above the page content area. In "split" mode, render the page body as a 2-column layout: left column shows zones (existing), right column shows the raw markdown/source text in a monospace scrollable pane. Default to "structured".
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`

### 2. Split-pane raw sources view

**Web2 spec**: When in "split" mode, the right pane shows the raw compiled source text with syntax highlighting for wikilinks and zone markers.
**Web-next currently**: Does not exist.
**What to do:** Create a `RawSourcePane` component that renders the page's raw text content. Highlight wikilinks (`[[slug]]`) inline using the existing `WikilinkPill` component styling. Show zone boundaries with subtle horizontal rules or background bands.
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`, `packages/plugin-mimir/src/ui/components/RawSourcePane.tsx` (new)

### 3. Action bar buttons (Edit, Flag, Promote confidence, Cite)

**Web2 spec**: Below the chip bar (type, confidence, mounts), a row of action buttons: "Edit" (opens zone edit), "Flag" (marks page for review), "Promote confidence" (bumps confidence level), "Cite" (copies citation to clipboard).
**Web-next currently**: The chip bar exists but no action buttons are rendered. The `mm-action-bar` div contains only the chip bar.
**What to do:** Add four action buttons to the `mm-action-bar`: Edit (triggers zone edit mode for the first zone), Flag (calls service method — stub if not implemented), Promote confidence (calls service method), Cite (copies page path + title to clipboard). Use the existing `mm-btn` styling migrated to Tailwind.
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`

### 4. Wikilink breaking detection

**Web2 spec**: Broken wikilinks (links to non-existent pages) are rendered with a red strikethrough and a tooltip "target not found". The tree sidebar also shows a warning indicator on pages that contain broken links.
**Web-next currently**: `WikilinkPill` has a `--broken` variant visually, but it is not automatically detected — it requires explicit `resolved` prop. No tree-level indicator exists.
**What to do:** In the zone renderer, automatically determine wikilink resolution status by checking if the target path exists in `allPages`. Pass `resolved={false}` to `WikilinkPill` when the target is missing. In `TreeNode`, add a small warning dot (amber) on pages that have at least one broken link.
**Files to modify:** `packages/plugin-mimir/src/ui/components/ZoneRenderers.tsx`, `packages/plugin-mimir/src/ui/components/TreeNode.tsx`

### 5. Migrate pages CSS to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Pages layout uses BEM classes from `mimir-views.css` (`.mm-pages-root`, `.mm-sidepanel`, `.mm-body`, `.mm-tree-*`).
**What to do:** Replace class-based styling in `PagesView.tsx` and `TreeNode.tsx` with Tailwind utilities using `niuu-` prefix. Remove dead CSS rules from `mimir-views.css`.
**Files to modify:** `packages/plugin-mimir/src/ui/PagesView.tsx`, `packages/plugin-mimir/src/ui/components/TreeNode.tsx`, `packages/plugin-mimir/src/ui/mimir-views.css`

---

## What to keep as-is

- 3-pane grid layout proportions (220px | 1fr | 280px)
- File tree sidebar with mount-merged union tree structure
- Zone editing with optimistic locking (full edit/save/cancel/error flow)
- MetaPanel content (provenance, sources, backlinks)
- Breadcrumb rendering
- `PageTypeGlyph` indicators in tree nodes

## Shared components

- `TreeNode` from `./components/TreeNode`
- `ZoneBlock` from `./components/ZoneBlock`
- `MetaPanel` from `./components/MetaPanel`
- `MountChip` from `./components/MountChip`
- `WikilinkPill` from `./components/WikilinkPill`
- `PageTypeGlyph` from `./components/PageTypeGlyph`

## Acceptance criteria

1. Reader layout toggle switches between structured and split views
2. Split view shows raw source text with highlighted wikilinks in a right pane
3. Action bar shows Edit, Flag, Promote confidence, and Cite buttons with correct behavior
4. Broken wikilinks are automatically detected and rendered with red strikethrough
5. Tree sidebar shows warning indicators on pages with broken links
6. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
7. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/pages-tree.png`
8. Zone editing flow remains functional (no regressions)
