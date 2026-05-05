# Registry Containment — Visual Parity with web2

**Visual test:** `e2e/visual/observatory.visual.spec.ts` → `observatory registry — containment tab matches web2`
**Status:** FAIL (6% pixel diff, threshold 5%)
**Web2 baseline:** `e2e/__screenshots__/web2/observatory/registry-containment.png`
**Web2 source:** `web2/niuu_handoff/flokk_observatory/design/registry.jsx` (ContainmentGraph), `styles.css:491-510`
**Web-next source:** `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (ContainmentTab, lines 207-374)

---

## Summary

The containment tree structure is functionally correct with proper drag-drop and data-attribute
states. The main visual gap is the hint/instruction box at the top, which in web2 has a styled
background, dashed border, and branded code highlights, while web-next renders it as a plain
paragraph with no background or border.

---

## Required changes

### 1. Style the hint box to match web2's `.containment-hint`

**Web2 spec** (`styles.css:507-508`):

```css
.containment-hint {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: var(--space-4);
  padding: 10px 12px;
  background: var(--color-bg-tertiary);
  border: 1px dashed var(--color-border-subtle);
  border-radius: var(--radius-sm);
}
.containment-hint code {
  background: color-mix(in srgb, var(--color-brand) 12%, transparent);
  color: var(--brand-300);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}
```

**Web-next currently** (`RegistryEditor.tsx:352-357`): Plain `<p>` with `niuu-m-0 niuu-mb-4 niuu-text-sm niuu-text-text-muted niuu-leading-[1.5]`. The `<code>` elements have basic mono styling but no background color or branded text.
**What to do:**

1. Change the `<p>` tag classes to include background, padding, border, and radius:
   - Add: `niuu-p-3 niuu-bg-bg-tertiary niuu-border niuu-border-dashed niuu-border-border-subtle niuu-rounded-sm`
   - Change text color from `niuu-text-text-muted` to `niuu-text-text-secondary`
   - Change font size from `niuu-text-sm` to `niuu-text-[13px]`
2. Style the `<code>` elements with brand background and color:
   - Replace existing code classes with: `niuu-font-mono niuu-text-[11px] niuu-px-[5px] niuu-py-[1px] niuu-rounded-[3px]`
   - Add inline style or custom class for `background: color-mix(in srgb, var(--color-brand) 12%, transparent); color: var(--brand-300)`
   - Or add a utility class to the plugin's CSS for `.containment-hint-code`

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (lines 352-357)

### 2. Match tree indentation styling with web2

**Web2 spec** (`styles.css:498`): `.tree-children { margin-left: 16px; border-left: 1px dashed var(--color-border-subtle); padding-left: 8px; }`
**Web-next currently** (`RegistryEditor.tsx:345`): Children rendered in a plain `<div>` with no border-left or indentation styling. Depth is tracked via `--tree-depth` CSS variable on each node.
**What to do:**

1. Verify the `registry-tree-node` CSS class (referenced at line 319) applies `padding-left: calc(var(--tree-depth) * 24px)` or equivalent indentation
2. Add a dashed left border to child containers: wrap children in a div with `niuu-ml-4 niuu-border-l niuu-border-dashed niuu-border-border-subtle niuu-pl-2`
3. Or update the plugin CSS file that defines `.registry-tree-node` children styles

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 345)
- CSS file defining `.registry-tree-node` (check for co-located CSS)

### 3. Verify drag state visuals match web2

**Web2 spec** (`styles.css:500-504`):

- `.tree-node.selected` → `background: color-mix(in srgb, var(--color-brand) 12%, transparent)`
- `.tree-node.dragging` → `opacity: 0.4; cursor: grabbing`
- `.tree-node.drop-ok` → `outline: 1px dashed color-mix(in srgb, var(--color-brand) 30%, transparent)`
- `.tree-node.drop-target` → `background: color-mix(in srgb, var(--color-brand) 20%, transparent); outline: 1px solid var(--color-brand); box-shadow: 0 0 0 2px color-mix(...)`
- `.tree-node.drop-invalid` → `background: color-mix(in srgb, var(--color-danger) 15%, transparent); outline: 1px solid var(--color-danger); cursor: not-allowed`
  **Web-next currently** (`RegistryEditor.tsx:315-318`): Uses `data-drag-state`, `data-selected`, `data-dragging` attributes. CSS applies styles via attribute selectors.
  **What to do:**

1. Confirm the CSS for `.registry-tree-node[data-drag-state="ok"]`, `[data-drag-state="target"]`, `[data-drag-state="invalid"]`, `[data-selected="true"]`, `[data-dragging="true"]` produces identical visual output to web2's class-based approach
2. Ensure the danger/invalid state uses `var(--color-critical)` (our token for red) not a hardcoded hex
3. Ensure `cursor: not-allowed` on invalid state, `cursor: grabbing` on dragging state

**Files to modify:**

- CSS file for `.registry-tree-node` (locate and verify)

### 4. Orphans section visual alignment

**Web2 spec** (`styles.css:509-510`): `.tree-orphans { margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px dashed var(--color-border-subtle); }` `.tree-orphan-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }`
**Web-next currently** (`RegistryEditor.tsx:362-370`): Uses `niuu-mt-6 niuu-pt-4 niuu-border-t niuu-border-border-subtle` — close but uses solid border instead of dashed.
**What to do:**

1. Add `niuu-border-dashed` to the orphans section container to match web2's dashed border-top
2. Adjust spacing: `niuu-mt-6` → `niuu-mt-4` and `niuu-pt-4` → `niuu-pt-3` to match `--space-4` / `--space-3`

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 363)

---

## What to keep as-is

| Feature                          | Reason                                                           |
| -------------------------------- | ---------------------------------------------------------------- |
| Data-attribute state management  | Cleaner than class toggling; same visual output when CSS matches |
| `dragIdRef` synchronous tracking | Fixes a real race condition in headless environments             |
| `isDescendant` cycle prevention  | Same logic as web2, just extracted to domain utility             |
| Orphan detection and rendering   | Matches web2 functionality                                       |

## Shared components

| Component             | Location       | Notes                                   |
| --------------------- | -------------- | --------------------------------------- |
| `ShapeSvg`            | `@niuulabs/ui` | Used in tree nodes                      |
| Tree node (composite) | plugin-local   | Only Observatory uses containment trees |

## Acceptance criteria

1. Hint box has `bg-tertiary` background, dashed border-subtle border, `border-radius-sm`, and padding
2. `<code>` elements inside the hint have branded background (`color-mix brand 12%`) and `brand-300` text color
3. Tree children have dashed left border with 16px left margin and 8px left padding
4. Drag states produce correct visual feedback matching web2 spec (selected, dragging, drop-ok, drop-target, drop-invalid)
5. Orphans section uses dashed border-top separator
6. Spacing matches web2 (`--space-4` margin-top, `--space-3` padding-top on orphans)
7. Visual test passes at <= 5% pixel diff against web2 baseline
8. All existing unit tests (including drag-drop tests) continue to pass
