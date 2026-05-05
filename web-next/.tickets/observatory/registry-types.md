# Registry Types — Visual Parity with web2

**Visual test:** `e2e/visual/observatory.visual.spec.ts` → `observatory registry — types tab matches web2`
**Status:** FAIL (6% pixel diff, threshold 5%)
**Web2 baseline:** `e2e/__screenshots__/web2/observatory/registry-types.png`
**Web2 source:** `web2/niuu_handoff/flokk_observatory/design/registry.jsx`, `styles.css:382-430`
**Web-next source:** `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (TypesTab, TypePreviewDrawer)

---

## Summary

The Types tab renders entity types as a vertical list of flex rows, while web2 renders them as
a responsive card grid (`repeat(auto-fill, minmax(280px, 1fr))`). The inspector panel is 320px
wide in web-next vs 380px in web2, and is read-only where web2 is fully editable. The layout
uses `display: flex` instead of web2's `display: grid` with fixed column widths.

---

## Required changes

### 1. Switch from flex-row list to card grid layout

**Web2 spec** (`styles.css:402`): `.type-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--space-3); }`
**Web-next currently** (`RegistryEditor.tsx:172`): Types rendered as `<div className="niuu-flex niuu-flex-col niuu-gap-1">` with each type as a full-width button row.
**What to do:**

1. Replace the flex-col container with a CSS grid: `niuu-grid niuu-gap-3` and add a custom class or `style` for `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`
2. Redesign each type item from a row button into a card matching web2's `.type-card` structure:
   - Card has `grid-template-columns: 36px 1fr auto` and `grid-template-rows: auto auto`
   - Shape swatch: 36x36px box with bg-tertiary background, border-subtle border, radius-sm
   - Type name row: label (13px, semibold) + rune (mono, brand color, 12px, bold)
   - Description row: 11px, muted, spans column 2
   - Meta column (right): type ID + shape info, mono 10px
3. Selected state: `border-color: color-mix(in srgb, var(--color-brand) 40%, transparent); background: color-mix(in srgb, var(--color-brand) 6%, var(--color-bg-secondary))`
4. Card base: `background: var(--color-bg-secondary); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4)`

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (TypesTab component, lines 150-204)

### 2. Change outer layout from flex to grid with 380px inspector column

**Web2 spec** (`styles.css:383-388`): `.registry { display: grid; grid-template-columns: 1fr 380px; height: 100%; overflow: hidden; }`
**Web-next currently** (`RegistryEditor.tsx:433`): `<div className="niuu-flex niuu-h-full niuu-overflow-hidden niuu-bg-bg-primary">` with drawer at `niuu-w-[320px]`.
**What to do:**

1. Change the outer RegistryEditor container from flex to grid: replace `niuu-flex` with `niuu-grid` and add `style={{ gridTemplateColumns: '1fr 380px' }}` or a custom Tailwind arbitrary value `niuu-grid-cols-[1fr_380px]`
2. Update `TypePreviewDrawer` width from `niuu-w-[320px]` to fill the grid column (remove explicit width, the grid column handles it)
3. When no type is selected, the grid collapses to single column (use conditional grid-cols)

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (lines 433, 28)

### 3. Add description paragraph below the header title

**Web2 spec** (`registry.jsx:41`): Below `<h2>` there is a `<p>` describing the registry purpose: "Every node that appears in the Observatory canvas is an instance of one of these types..."
**Web-next currently** (`RegistryEditor.tsx:439`): Only has the title `<h2>` and version meta; no description paragraph.
**What to do:**

1. Add a paragraph below the h2: `<p className="niuu-m-0 niuu-text-text-secondary niuu-text-sm niuu-max-w-[64ch]">Every node that appears in the Observatory canvas is an instance of one of these types. Edit a type here and the canvas re-renders.</p>`
2. Match web2 styling: `color: var(--color-text-secondary); font-size: var(--text-sm); max-width: 64ch`

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 439, inside header)

### 4. Move search input into tab bar (inline with tabs)

**Web2 spec** (`registry.jsx:55-58`): The filter input sits inside `.registry-tabs` div, right-aligned with `flex: 1` spacer pushing it to the right, only visible on the types tab.
**Web-next currently** (`RegistryEditor.tsx:152-159`): Search input is inside the TypesTab panel content, above the type list.
**What to do:**

1. Move the search input out of TypesTab and into the tab bar row in RegistryEditor
2. Add a spacer (`niuu-flex-1`) between the last tab button and the input
3. Only show the input when `activeTab === 'types'`
4. Style: height 32px, width 220px, matching web2 inline appearance

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (tab bar section, lines 451-467; TypesTab lines 152-159)

---

## What to keep as-is

| Feature                                               | Reason                                                                                  |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Read-only TypePreviewDrawer                           | Editing is deferred to a future ticket; visual test only checks layout not interactions |
| Chip components from `@niuulabs/ui`                   | Better than web2's inline chip divs                                                     |
| `useRegistryEditor` hook                              | Clean state management; no visual impact                                                |
| Tab accessibility (`role="tablist"`, `aria-selected`) | Improvement over web2                                                                   |

## Shared components

| Component       | Location       | Notes                              |
| --------------- | -------------- | ---------------------------------- |
| `ShapeSvg`      | `@niuulabs/ui` | Already shared, used in type cards |
| `Chip`          | `@niuulabs/ui` | Already shared, used in drawer     |
| Type card (new) | plugin-local   | Only Observatory uses it           |

## Acceptance criteria

1. Types tab displays types in a responsive card grid matching `repeat(auto-fill, minmax(280px, 1fr))`
2. Each card shows shape swatch (36x36), label + rune, description, and meta (ID + shape)
3. Selected card has brand-tinted border and background
4. Inspector/drawer panel is 380px wide (grid column)
5. Description paragraph visible below the header title
6. Search input sits inline in the tab bar, right-aligned, only on types tab
7. Outer layout uses CSS grid (`1fr 380px`) not flexbox
8. Visual test passes at <= 5% pixel diff against web2 baseline
9. All existing unit tests continue to pass
