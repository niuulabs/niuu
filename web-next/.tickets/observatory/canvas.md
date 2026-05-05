# Canvas — Visual Parity with web2

**Visual test:** `e2e/visual/observatory.visual.spec.ts` → `observatory canvas matches web2`
**Status:** FAIL (6% pixel diff, threshold 5%)
**Web2 baseline:** `e2e/__screenshots__/web2/observatory/canvas.png`
**Web2 source:** `web2/niuu_handoff/flokk_observatory/design/observatory.jsx`, `styles.css`
**Web-next source:** `packages/plugin-observatory/src/ui/ObservatoryPage.tsx`, `ObservatoryTopbar.tsx`, `ObservatorySubnav.tsx`, `overlays/*`

---

## Summary

ObservatoryPage.tsx uses unprefixed Tailwind classes (`fixed inset-0 flex flex-col`) which will
not compile correctly under the `niuu-` prefix rule. Additionally, the page renders as a
standalone full-viewport div when it should render as content-only (the Shell provides the
outer frame), and topbar stats are rendered in a separate slot component but the page layout
itself references obsolete positioning from a pre-Shell era.

---

## Required changes

### 1. Fix unprefixed Tailwind classes in ObservatoryPage.tsx

**Web2 spec** (`styles.css:171`): Content sits inside `.content { grid-area: content; position: relative; background: var(--color-bg-primary); overflow: hidden; }` — the shell grid positions it.
**Web-next currently** (`ObservatoryPage.tsx:45`): Uses `className="fixed inset-0 flex flex-col"` — unprefixed and uses `fixed` positioning which conflicts with Shell content slot.
**What to do:**

1. Replace `fixed inset-0 flex flex-col` with `niuu-relative niuu-flex niuu-flex-col niuu-h-full niuu-overflow-hidden`
2. The Shell content area already provides full-height context; the page should fill it rather than fight it with `position: fixed`
3. Audit all other classes in the file (`flex-1 min-h-0`, `sr-only`) and prefix them

**Files to modify:**

- `packages/plugin-observatory/src/ui/ObservatoryPage.tsx`

### 2. Fix canvas overlay positioning to match web2

**Web2 spec** (`styles.css:191-194`):

- `.overlay-topleft` → `position: absolute; top: var(--space-4); left: var(--space-4);`
- `.overlay-bottomright` → `position: absolute; bottom: var(--space-4); right: var(--space-4);`
- `.eventlog` → `position: absolute; bottom: 0; left: 0; right: 0; height: 110px;`
- `.drawer` → `position: absolute; top: 0; right: 0; bottom: 0; width: 340px;`
  **Web-next currently** (`overlays/*.tsx`): Overlays use CSS files (`ConnectionLegend.css`, `EventLog.css`, `Minimap.css`) that should already replicate these positions. Verify they use identical offsets.
  **What to do:**

1. Confirm `ConnectionLegend.css` positions the legend at `top: var(--space-4); left: var(--space-4)` (top-left overlay)
2. Confirm `EventLog.css` positions the log at `bottom: 0; left: 0; right: 0; height: 110px` with inner panel right-offset of 320px (web2 uses `right: 320px`)
3. Confirm `Minimap` is at bottom-right (`bottom: var(--space-4); right: var(--space-4)`)
4. Confirm `EntityDrawer` is `position: absolute; top: 0; right: 0; bottom: 0; width: 340px` (web2 uses 340px)
5. The parent container must have `position: relative` for absolute overlays to anchor correctly — validate after fixing point 1

**Files to modify:**

- `packages/plugin-observatory/src/ui/overlays/ConnectionLegend.css`
- `packages/plugin-observatory/src/ui/overlays/EventLog.css`
- `packages/plugin-observatory/src/ui/overlays/Minimap.css` (if it exists) or `Minimap.tsx`
- `packages/plugin-observatory/src/ui/overlays/EntityDrawer.css` (if it exists) or `EntityDrawer.tsx`

### 3. Ensure TopologyCanvas fills available space

**Web2 spec** (`styles.css:188-189`): `.canvas-wrap { position: absolute; inset: 0; }` `.canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; cursor: grab; }`
**Web-next currently** (`ObservatoryPage.tsx:47-50`): Canvas receives `className="flex-1 min-h-0"` (unprefixed)
**What to do:**

1. Change to `niuu-flex-1 niuu-min-h-0` at minimum
2. TopologyCanvas should render with `position: relative` wrapper so its `<canvas>` element can be `position: absolute; inset: 0` matching web2
3. Verify the `data-testid="topology-canvas"` element is the visible canvas the visual test waits for

**Files to modify:**

- `packages/plugin-observatory/src/ui/ObservatoryPage.tsx`
- `packages/plugin-observatory/src/ui/TopologyCanvas/TopologyCanvas.tsx`

### 4. Verify topbar stats render within the Shell topbar slot

**Web2 spec** (`styles.css:213-226`): Stats strip sits in `.topbar-right` as flex items with mono font, 11px, accented strong values.
**Web-next currently** (`ObservatoryTopbar.tsx`): Renders correctly as a separate component with proper CSS classes. This component is exposed as the `topbarRight` slot in the plugin descriptor.
**What to do:**

1. Verify the plugin descriptor wires `ObservatoryTopbar` to the Shell's `topbarRight` slot
2. Confirm it renders during the visual test (the test navigates to `/observatory` and waits for `[data-testid="topology-canvas"]` but does not assert on the topbar explicitly — it must be visible in the screenshot)
3. If the topbar slot is not rendered by the time the screenshot fires, add a wait selector or verify mock data includes topology nodes

**Files to modify:**

- `packages/plugin-observatory/src/index.ts` (plugin descriptor)
- `ObservatoryTopbar.css` (verify matches `.stats` styling from web2)

---

## What to keep as-is

| Feature                                   | Reason                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| Accessible `sr-only` node list            | Keyboard/screen-reader alternative not in web2 — accessibility improvement |
| `useObservatoryStore` state sharing       | Cleaner than web2's prop-drilling; same visual output                      |
| Separate CSS files per overlay            | Better maintainability vs web2's monolith `styles.css`                     |
| ObservatorySubnav as standalone component | Correct composition for Shell subnav slot                                  |

## Shared components

| Component          | Location                 | Used by                       |
| ------------------ | ------------------------ | ----------------------------- |
| `ConnectionLegend` | plugin-local `overlays/` | Observatory only              |
| `Minimap`          | plugin-local `overlays/` | Observatory only              |
| `EventLog`         | plugin-local `overlays/` | Observatory only              |
| `EntityDrawer`     | plugin-local `overlays/` | Observatory only              |
| `LiveBadge`        | `@niuulabs/ui`           | Shell topbar (already shared) |
| `StateDot`         | `@niuulabs/ui`           | Multiple plugins              |

## Acceptance criteria

1. All Tailwind classes in `ObservatoryPage.tsx` use the `niuu-` prefix
2. Page renders as content-fill (not `position: fixed`) within the Shell content slot
3. ConnectionLegend anchored top-left with `--space-4` offset
4. Minimap anchored bottom-right with `--space-4` offset
5. EventLog anchored at bottom, spanning full width minus drawer area (right offset 320px)
6. EntityDrawer slides in from right, width 340px, full height
7. TopologyCanvas fills available space with `cursor: grab`
8. Topbar stats (realms/ravens/raids) visible in screenshot
9. Visual test passes at <= 5% pixel diff against web2 baseline
10. All existing unit tests continue to pass
