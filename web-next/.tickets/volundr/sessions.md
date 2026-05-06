# Sessions — Visual Parity with web2

**Visual test:** `e2e/visual/volundr.visual.spec.ts` → `sessions`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/volundr/sessions.png`
**Web2 source:** `web2/niuu_handoff/volundr/design/sessions.jsx`, `shell.jsx`
**Web-next source:** `packages/plugin-volundr/src/ui/SessionsPage.tsx`

---

## Summary

The Sessions page has the lowest parity (75%) due to a structural layout difference: web2 uses a left sidebar subnav tree for filtering sessions by status/template/cluster, while web-next replaces this with horizontal top tabs. Additionally, the search/filter input and issue link in the header are missing.

---

## Required changes

### 1. Left sidebar subnav tree

**Web2 spec**: The sessions page has a 240px-wide left sidebar containing a tree navigation with collapsible sections: "By Status" (Active, Completed, Failed, Cancelled), "By Template" (list of template names), and "By Cluster" (list of cluster names). Each node shows a count badge. Clicking a node filters the session list. The active node has a subtle left border accent and lighter background.
**Web-next currently**: Filtering is done via horizontal tabs at the top of the page (Active | Completed | All), with no template or cluster grouping.
**What to do:** Replace the top tabs with a left sidebar tree. Create a `<SessionsSidebar />` component. Layout: `niuu-w-60 niuu-shrink-0 niuu-border-r niuu-border-zinc-800 niuu-pr-4 niuu-overflow-y-auto`. Each section header: `niuu-text-xs niuu-font-semibold niuu-text-zinc-500 niuu-uppercase niuu-tracking-wider niuu-mb-2 niuu-mt-4`. Each tree node: `niuu-flex niuu-items-center niuu-justify-between niuu-px-3 niuu-py-1.5 niuu-rounded-md niuu-text-sm niuu-cursor-pointer hover:niuu-bg-zinc-800/50`. Active node: `niuu-bg-zinc-800 niuu-border-l-2 niuu-border-indigo-500`. Count badge: `niuu-text-xs niuu-text-zinc-500 niuu-bg-zinc-800 niuu-px-1.5 niuu-rounded-full`. The main content area shifts right to accommodate the sidebar.
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionsPage.tsx` (layout restructure)
- `packages/plugin-volundr/src/ui/components/SessionsSidebar.tsx` (new)
- `packages/plugin-volundr/src/ui/components/SessionsNavTree.tsx` (new)

### 2. Search/filter input

**Web2 spec**: Above the session list (but below the page header), there is a search input with a magnifying glass icon. It filters sessions by name, ID, or template in real-time as the user types. The input spans the full width of the content area with a subtle border and rounded corners.
**Web-next currently**: No search/filter input exists on the sessions page.
**What to do:** Add a search input above the session list. Use `niuu-w-full niuu-px-3 niuu-py-2 niuu-bg-zinc-900 niuu-border niuu-border-zinc-800 niuu-rounded-lg niuu-text-sm niuu-text-zinc-200 niuu-placeholder-zinc-500 focus:niuu-outline-none focus:niuu-border-zinc-700`. Include a search icon (from Lucide or the design system icon set) positioned absolutely on the left: `niuu-absolute niuu-left-3 niuu-top-1/2 niuu--translate-y-1/2 niuu-text-zinc-500 niuu-w-4 niuu-h-4`. The input should have left padding to accommodate the icon. Wire to local filter state.
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionsPage.tsx`
- `packages/plugin-volundr/src/ui/components/SessionSearchInput.tsx` (new)

### 3. Issue link in page header

**Web2 spec**: The sessions page header (top-right, next to any existing actions) shows a link icon + "NIU-XXX" text that links to the associated project issue tracker. It appears as a subtle, clickable text link with an external-link icon.
**Web-next currently**: The page header has no issue link reference.
**What to do:** Add an issue link element in the header's right-aligned action area. Use `niuu-flex niuu-items-center niuu-gap-1 niuu-text-xs niuu-text-zinc-500 hover:niuu-text-zinc-300 niuu-transition-colors`. Render a link/external-link icon (16x16) followed by the issue key text. Link to the configured issue tracker URL. This may be optional/conditional based on whether an issue is associated.
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionsPage.tsx`

---

## What to keep as-is

| Element                                                  | Reason               |
| -------------------------------------------------------- | -------------------- |
| Session list card design (name, status, duration, model) | Already matches web2 |
| Session status badge colours                             | Already correct      |
| Pagination / infinite scroll at bottom                   | Already matches web2 |
| Page title and breadcrumb                                | Already correct      |
| Session card click-to-navigate behaviour                 | Already working      |

## Shared components

| Component                     | Source                                            |
| ----------------------------- | ------------------------------------------------- |
| `Input` / `SearchInput`       | Check `@niuulabs/ui`; if absent, plugin-local     |
| `Badge`                       | `@niuulabs/ui` — for count badges in sidebar tree |
| `TreeNav` / `NavTree`         | Plugin-local — specific to sessions filtering     |
| `Icon` (Search, ExternalLink) | `@niuulabs/ui` or Lucide icons                    |

## Acceptance criteria

1. Sessions page uses a left sidebar tree (240px) for filtering by status, template, and cluster
2. Each sidebar tree node shows a count badge and highlights when active
3. Sidebar sections are collapsible with a disclosure toggle
4. A full-width search input with icon allows real-time filtering of the session list
5. Page header includes an issue link (icon + key) when an issue is associated
6. Main content area correctly fills remaining width beside the sidebar
7. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
8. Visual test passes with <=5% pixel diff against web2 baseline
9. No regressions to existing passing visual tests
