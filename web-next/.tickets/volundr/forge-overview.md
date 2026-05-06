# Forge Overview — Visual Parity with web2

**Visual test:** `e2e/visual/volundr.visual.spec.ts` → `forge-overview`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/volundr/forge-overview.png`
**Web2 source:** `web2/niuu_handoff/volundr/design/forge.jsx`
**Web-next source:** `packages/plugin-volundr/src/ui/ForgePage.tsx`

---

## Summary

The Forge overview page is missing several data-dense UI elements present in the web2 prototype: boot progress bars on inflight pods, CLI/IDE badge indicators, token/cost inline stats on pod cards, sparkline charts in KPI tiles, usage counts on quick-launch cards, and preview text on chronicle tail entries.

---

## Required changes

### 1. Boot progress bar on inflight pods

**Web2 spec**: Pods in `booting` or `initializing` state show a thin horizontal progress bar at the bottom of the card, animated from 0-100% with a pulsing gradient in the pod's accent colour.
**Web-next currently**: Inflight pods show only a status badge with no progress indication.
**What to do:** Add a `<BootProgressBar />` component rendered inside the pod card when `pod.status` is `booting` or `initializing`. Use `niuu-h-1 niuu-rounded-full niuu-bg-gradient-to-r` with the appropriate accent colour token. Accept a `progress` prop (0-100) and animate width via inline `style={{ width }}` (only dynamic value allowed).
**Files to modify:**

- `packages/plugin-volundr/src/ui/ForgePage.tsx`
- `packages/plugin-volundr/src/ui/components/PodCard.tsx` (new or existing)
- `packages/plugin-volundr/src/ui/components/BootProgressBar.tsx` (new)

### 2. CLI badge on pod cards

**Web2 spec**: Each pod card shows a small rounded badge (top-right) indicating the connection type — "CLI", "IDE", or "API" — with a muted background and monospace font.
**Web-next currently**: No connection-type badge is rendered.
**What to do:** Add a badge element inside the pod card header. Use `niuu-text-xs niuu-font-mono niuu-px-1.5 niuu-py-0.5 niuu-rounded niuu-bg-zinc-800 niuu-text-zinc-400`. Derive the label from `pod.connectionType` or similar field.
**Files to modify:**

- `packages/plugin-volundr/src/ui/components/PodCard.tsx`

### 3. Token/cost inline stats on pod cards

**Web2 spec**: Below the pod name/model line, a secondary row shows `tokens: 12.4k` and `cost: $0.08` in muted text, separated by a middle dot.
**Web-next currently**: Pod cards show model and status but no token/cost metrics.
**What to do:** Add a stats row after the model label. Render `pod.tokensUsed` (formatted with k/M suffix) and `pod.cost` (formatted as USD). Use `niuu-text-xs niuu-text-zinc-500 niuu-flex niuu-items-center niuu-gap-1`.
**Files to modify:**

- `packages/plugin-volundr/src/ui/components/PodCard.tsx`

### 4. Sparkline in KPI tiles

**Web2 spec**: Each KPI tile (Active Pods, Total Sessions, Avg Latency, etc.) includes a 48x16px sparkline SVG showing the last 24 data points, rendered in the tile's accent colour at 50% opacity.
**Web-next currently**: KPI tiles show only the numeric value and label.
**What to do:** Create a `<Sparkline />` component that accepts a `data: number[]` prop and renders an inline SVG polyline. Place it in the bottom-right of each KPI tile. Use `niuu-absolute niuu-bottom-2 niuu-right-2 niuu-opacity-50`. The SVG stroke colour should match the tile's accent token.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ForgePage.tsx`
- `packages/plugin-volundr/src/ui/components/Sparkline.tsx` (new)
- `packages/plugin-volundr/src/ui/components/KpiTile.tsx` (new or existing)

### 5. Usage count on quick-launch cards

**Web2 spec**: Quick-launch template cards display a small usage count (`Used 42x`) in the bottom-left corner in muted text.
**Web-next currently**: Quick-launch cards show template name and icon only.
**What to do:** Add a usage count label. Use `niuu-text-xs niuu-text-zinc-500 niuu-mt-auto`. Source the count from `template.usageCount`.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ForgePage.tsx`
- `packages/plugin-volundr/src/ui/components/QuickLaunchCard.tsx` (if exists)

### 6. Preview text on chronicle tail entries

**Web2 spec**: The chronicle/activity feed at the bottom of the page shows each entry with a one-line preview of the last message or action (truncated to 80 chars with ellipsis).
**Web-next currently**: Chronicle entries show timestamp + event type but no preview content.
**What to do:** Add a preview line below the event header. Use `niuu-text-xs niuu-text-zinc-500 niuu-truncate niuu-max-w-md`. Source from `entry.preview` or `entry.lastMessage`.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ForgePage.tsx`
- `packages/plugin-volundr/src/ui/components/ChronicleEntry.tsx` (if exists)

---

## What to keep as-is

| Element                               | Reason                       |
| ------------------------------------- | ---------------------------- |
| Pod card layout (grid vs list toggle) | Already matches web2         |
| Status badge colours                  | Already correct              |
| Page header with breadcrumb           | Already matches web2         |
| Responsive grid breakpoints           | Already matches web2         |
| Dark theme base colours               | Already using correct tokens |

## Shared components

| Component         | Source                                                              |
| ----------------- | ------------------------------------------------------------------- |
| `Badge`           | `@niuulabs/ui` — reuse for CLI/IDE/API badge                        |
| `Sparkline`       | Plugin-local — too specific for shared lib                          |
| `BootProgressBar` | Plugin-local — Volundr-specific                                     |
| `KpiTile`         | Evaluate if `@niuulabs/ui` has a `StatCard`; otherwise plugin-local |

## Acceptance criteria

1. Inflight pods display an animated progress bar reflecting boot progress percentage
2. Each pod card shows a CLI/IDE/API connection-type badge in the top-right corner
3. Pod cards display token count and cost as inline stats below the model name
4. KPI tiles render a sparkline SVG showing recent trend data
5. Quick-launch cards show a usage count in muted text
6. Chronicle tail entries include a truncated preview of the last message/action
7. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
8. Visual test passes with <=5% pixel diff against web2 baseline
9. No regressions to existing passing visual tests
