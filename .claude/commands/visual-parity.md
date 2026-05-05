---
name: visual-parity
description: Compare a web-next plugin page against its web2 baseline screenshot and fix visual differences. Use when a page needs to match its web2 prototype.
argument-hint: <plugin>/<page> (e.g. mimir/lint, mimir/graph, tyr/dashboard)
---

# Visual Parity: $0

You are aligning a web-next plugin page to match its web2 baseline screenshot.

## Critical rules

- **NEVER guess what the target looks like.** Your image viewer renders screenshots as compressed thumbnails. You MUST crop the baseline into sections using `sips` to see actual details.
- **plugin-mimir uses tsup.** After editing source files, you MUST run `pnpm --filter @niuulabs/plugin-mimir build` (or the relevant plugin filter) before Playwright will reflect changes.
- **Use pnpm, never npx.** This project uses pnpm exclusively.
- **Match existing component patterns.** Before writing button/card/layout styles, grep for how other pages in the same plugin style their equivalent elements (e.g. `ACTION_BTN_BASE`, `mm-btn`, `FILTER_BTN_BASE`). Reuse those patterns.

## Step 1: Locate the baseline and source

Find the web2 baseline screenshot:
```
web-next/e2e/__screenshots__/web2/<plugin>/<page>.png
```

Find the page component:
```
web-next/packages/plugin-<plugin>/src/ui/<PageName>.tsx
```

Read both files. Also read the corresponding test file and CSS file if they exist.

## Step 2: Crop the baseline into readable sections

The baseline is 1440x900. Crop it into sections so you can actually read the content:

```bash
# Top area (stat cards, headers) — adjust offsets per page
sips --cropToHeightWidth 200 1200 --cropOffset 30 240 <baseline>.png --out /tmp/target-top.png

# Middle content area
sips --cropToHeightWidth 400 1200 --cropOffset 200 240 <baseline>.png --out /tmp/target-middle.png

# Right-side elements (buttons, overlays, info cards)
sips --cropToHeightWidth 500 300 --cropOffset 200 1140 <baseline>.png --out /tmp/target-right.png
```

Read each cropped image. Write down every UI element you see: layout structure, text content, button labels, column headers, card contents, overlay positions.

## Step 3: Capture the current state

Build the plugin and run the specific visual test:

```bash
cd web-next
pnpm --filter @niuulabs/plugin-<plugin> build
pnpm playwright test e2e/visual/mimir.visual.spec.ts -g "<page>" --reporter=list
```

Read the actual screenshot from `test-results/` (if the test failed) to see the current state.

## Step 4: List every difference

Compare section by section. For each difference, note:
- What the target shows
- What the current page shows
- Which component/element needs to change

Present the full list to the user and **wait for sign-off before proceeding**.

## Step 5: Implement fixes

Edit the page component, CSS, and tests. Key patterns:

- **Full-bleed layouts**: Use `position: relative` wrapper with `width: 100%; height: 100%` and absolute-positioned overlays
- **Floating overlays**: CSS class with `position: absolute; z-index: 1` + corner positioning
- **Ghost buttons**: Use `ACTION_BTN` pattern from RoutingPage — `niuu-bg-transparent niuu-border-border-subtle niuu-text-text-secondary`
- **Brand buttons**: `niuu-bg-brand niuu-border-brand niuu-text-bg-primary`
- **KPI strips**: Full-width `grid-cols-N` with `gap: 1px` or border separators, subtitles showing contributing rules
- **Two-column layouts**: `grid-cols-[220px_1fr]` with sidebar + content panel

## Step 6: Build, test, verify

```bash
# Run unit tests
pnpm vitest run packages/plugin-<plugin>/src/ui/<PageName>.test.tsx --reporter=verbose

# Build the plugin (REQUIRED for Playwright to see changes)
pnpm --filter @niuulabs/plugin-<plugin> build

# Run visual test
pnpm playwright test e2e/visual/<plugin>.visual.spec.ts -g "<page>" --reporter=list
```

If the visual test passes, read the snapshot. If it fails, read the actual screenshot from `test-results/` and show it to the user for feedback.

## Step 7: Iterate

If the user reports remaining differences, crop the specific area of the baseline again, compare to the current screenshot, and fix. Always rebuild the plugin before re-running Playwright.
