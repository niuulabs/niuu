# Registry JSON — Visual Parity with web2

**Visual test:** `e2e/visual/observatory.visual.spec.ts` → `observatory registry — JSON tab matches web2`
**Status:** FAIL (6% pixel diff, threshold 5%)
**Web2 baseline:** `e2e/__screenshots__/web2/observatory/registry-json.png`
**Web2 source:** `web2/niuu_handoff/flokk_observatory/design/registry.jsx:91-93`, `styles.css:512-529`
**Web-next source:** `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (JsonTab, lines 376-418)

---

## Summary

The JSON viewer has two visual gaps: (1) it uses `bg-secondary` background instead of web2's
`bg-primary` (darker), and (2) the height constraints differ — web2 uses `min-height: 480px`
with `max-height: calc(100vh - 240px)` while web-next uses only `max-h-[600px]`. The copy
button is a web-next improvement that should be preserved.

---

## Required changes

### 1. Fix background color from `bg-secondary` to `bg-primary`

**Web2 spec** (`styles.css:514`): `.json-view { background: var(--color-bg-primary); }`
**Web-next currently** (`RegistryEditor.tsx:412`): `<pre>` has class `niuu-bg-bg-secondary`
**What to do:**

1. Change `niuu-bg-bg-secondary` to `niuu-bg-bg-primary` on the `<pre>` element
2. This makes the JSON viewer background match the darkest surface (zinc-950), giving it the
   "sunken" code-block appearance that web2 has

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 412)

### 2. Fix height constraints to match web2

**Web2 spec** (`styles.css:521-522`): `min-height: 480px; max-height: calc(100vh - 240px);`
**Web-next currently** (`RegistryEditor.tsx:412`): Only `niuu-max-h-[600px]` — no min-height, and max-height is fixed rather than viewport-relative.
**What to do:**

1. Replace `niuu-max-h-[600px]` with `niuu-min-h-[480px] niuu-max-h-[calc(100vh-240px)]`
2. This ensures the JSON block has substantial minimum height (avoids a collapsed look with
   short registries) while capping at a viewport-aware maximum (avoids overflow for large ones)
3. Keep `niuu-overflow-y-auto` to handle content exceeding max-height

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 412)

### 3. Match typography details

**Web2 spec** (`styles.css:513-514`): `font-family: var(--font-mono); font-size: 11px; line-height: 1.55; color: var(--color-text-secondary);`
**Web-next currently** (`RegistryEditor.tsx:412`): `niuu-font-mono niuu-text-xs niuu-text-text-secondary niuu-leading-[1.6]`
**What to do:**

1. `niuu-text-xs` maps to `0.75rem` (12px) which is close to web2's 11px. For exact match, change to `niuu-text-[11px]`
2. `niuu-leading-[1.6]` vs web2's `1.55` — change to `niuu-leading-[1.55]`
3. These are subtle differences but they compound across hundreds of lines of JSON output and affect the pixel diff

**Files to modify:**

- `packages/plugin-observatory/src/ui/RegistryEditor.tsx` (line 412)

### 4. Match border and whitespace styling

**Web2 spec** (`styles.css:515-519`): `border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); padding: var(--space-4); white-space: pre; overflow: auto;`
**Web-next currently** (`RegistryEditor.tsx:412`): Has `niuu-border niuu-border-border-subtle niuu-rounded-md niuu-p-4 niuu-overflow-x-auto` — this is correct. Also has `niuu-m-0`.
**What to do:**

1. Verify `niuu-overflow-x-auto` is sufficient. Web2 uses `overflow: auto` (both axes). Since we also have `niuu-overflow-y-auto`, both axes are covered — confirm they don't conflict.
2. Confirm `white-space: pre` is applied (the `<pre>` tag provides this by default, so no class needed)
3. No changes needed here if `<pre>` default behavior applies — validate only.

**Files to modify:**

- (Verification only, likely no changes)

---

## What to keep as-is

| Feature                                        | Reason                                                                     |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| Copy JSON button                               | UX improvement not in web2 — useful for developers inspecting the registry |
| `niuu-relative` wrapper for button positioning | Required by the absolute-positioned copy button                            |
| `copied!` state feedback                       | Good UX, no visual test impact (button is small)                           |
| `JSON.stringify(registry, null, 2)` formatting | Same output as web2                                                        |

## Shared components

| Component               | Location     | Notes                                                                           |
| ----------------------- | ------------ | ------------------------------------------------------------------------------- |
| Copy button pattern     | plugin-local | Could promote to `@niuulabs/ui` as `CodeBlock` if Mimir or Volundr need similar |
| JSON viewer (composite) | plugin-local | Only Observatory uses it currently                                              |

## Acceptance criteria

1. JSON `<pre>` block has `bg-primary` background (darkest surface, matching web2)
2. Height constraints are `min-height: 480px` and `max-height: calc(100vh - 240px)`
3. Font size is 11px with line-height 1.55
4. Border is 1px solid border-subtle with radius-md
5. Copy button remains visible and functional (top-right corner)
6. JSON content is scrollable on both axes when content overflows
7. Visual test passes at <= 5% pixel diff against web2 baseline
8. All existing unit tests continue to pass
