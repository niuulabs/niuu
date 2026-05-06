# Graph (/mimir/graph) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir graph matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/graph.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (GraphView section)
**Web-next source:** `packages/plugin-mimir/src/ui/GraphPage.tsx`, `packages/plugin-mimir/src/ui/GraphPage.css`

---

## Summary

Web2 uses a radial category-grouped layout with sine jitter for node positioning and a hover glow effect on nodes. Web-next uses a simple circle layout (equal-angle distribution). The focus/hop controls and stats row in web-next are improvements to keep. Key gaps: force-directed (or category-radial) layout and hover glow effect.

---

## Required changes

### 1. Force-directed / category-radial layout

**Web2 spec**: Nodes are grouped by category into radial clusters. Each category occupies a sector of the circle. Within each sector, nodes are offset with a sine-based jitter to avoid overlap. This creates a visually organic, clustered graph rather than a uniform circle.
**Web-next currently**: All nodes are placed in a simple circle with equal angular spacing, regardless of category. No clustering or grouping.
**What to do:** Replace `layoutCircle()` with a `layoutCategoryRadial()` function that:

1. Groups nodes by category
2. Assigns each category a sector of the full circle (proportional to node count)
3. Within each sector, distributes nodes with radial jitter (randomized offset from the sector's center radius, seeded by node index for determinism)
4. Keeps the same SVG viewBox (600x440) and center point (300, 220)

This does not need to be a full physics simulation — the deterministic radial-cluster layout from web2 is sufficient. The layout should be stable across re-renders (no animation/physics loop).
**Files to modify:** `packages/plugin-mimir/src/ui/GraphPage.tsx`

### 2. Hover glow effect on nodes

**Web2 spec**: Hovering a node applies a glow effect — an SVG filter (`feGaussianBlur` + `feComposite`) that creates a soft colored halo around the node circle matching its category color. The glow is 3-4px spread.
**Web-next currently**: No hover effect on nodes (only cursor changes to pointer via the `role="button"`).
**What to do:** Add an SVG `<defs>` section with a glow filter. On hover (CSS `:hover` on the node `<g>`), apply the filter via a class. The glow color should match the node's category fill. Since SVG filters can't easily use dynamic colors via CSS alone, use a single white/light glow filter and rely on `opacity` + the existing fill for the visual effect.
**Files to modify:** `packages/plugin-mimir/src/ui/GraphPage.tsx`

### 3. Edge rendering improvements

**Web2 spec**: Edges in web2 have a subtle gradient opacity (stronger near source, fading toward target) and are rendered behind nodes with a low opacity (0.15-0.2). Focused-node edges are highlighted brighter.
**Web-next currently**: Edges are simple lines with a uniform class `.graph-page__edge` (likely a single stroke color/opacity).
**What to do:** Add opacity differentiation: default edges at ~0.15 opacity, edges connected to the focused node at ~0.5 opacity. Optionally add a gradient stroke if it improves visual match. Implement via conditional class or inline style on focused edges.
**Files to modify:** `packages/plugin-mimir/src/ui/GraphPage.tsx`

### 4. Migrate GraphPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Graph page uses `GraphPage.css` with BEM classes (`.graph-page`, `.graph-page__svg`, `.graph-page__edge`, `.graph-page__node`, etc.).
**What to do:** Replace class-based styling with Tailwind utilities using `niuu-` prefix. For SVG-specific styling that Tailwind cannot express (e.g. stroke, fill, filter), use a minimal co-located `.css` file with `@apply` or CSS custom properties — but prefer inline Tailwind where possible. Delete `GraphPage.css`.
**Files to modify:** `packages/plugin-mimir/src/ui/GraphPage.tsx`, `packages/plugin-mimir/src/ui/GraphPage.css` (delete or minimize)

---

## What to keep as-is

- Focus node input and clear button
- Hop count selector (1-4 buttons)
- Stats row (node count, edge count, focus info chips)
- Category legend with color dots
- Node click-to-focus behavior
- Keyboard accessibility (Enter/Space to select nodes)
- Loading state with `StateDot`
- Error state rendering
- SVG viewBox dimensions (600x440)

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`

## Acceptance criteria

1. Nodes are clustered by category in radial sectors (not uniformly distributed)
2. Hovering a node shows a glow effect
3. Focused-node edges are visually highlighted; unfocused edges are subtle (~0.15 opacity)
4. Layout is deterministic (same data produces same visual)
5. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
6. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/graph.png`
7. Focus, hop controls, legend, and stats row remain functional
8. No regressions in keyboard accessibility
