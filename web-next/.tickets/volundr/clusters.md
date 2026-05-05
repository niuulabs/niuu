# Clusters — Visual Parity with web2

**Visual test:** `e2e/visual/volundr.visual.spec.ts` → `clusters`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/volundr/clusters.png`
**Web2 source:** `web2/niuu_handoff/volundr/design/pages.jsx`
**Web-next source:** `packages/plugin-volundr/src/ui/ClustersPage.tsx`

---

## Summary

The Clusters page is missing the full cluster detail header (kind/realm/status + action buttons), a disk resource panel that was replaced with a pods slot, and the pods panel currently renders mock data instead of real cluster pod information.

---

## Required changes

### 1. Cluster detail header with kind/realm/status and actions

**Web2 spec**: When a cluster is selected, a detail header spans the full width showing: cluster name (large), kind badge (e.g. "k3s", "eks"), realm badge (e.g. "production", "staging"), status indicator (dot + label), and right-aligned action buttons: "Cordon" (warning style), "Drain" (danger style), and "Forge Here" (primary style). The header has a subtle bottom border separating it from the content panels below.
**Web-next currently**: The cluster detail view shows only the cluster name as a heading with no metadata badges or action buttons.
**What to do:** Build a `<ClusterDetailHeader />` component. Layout: `niuu-flex niuu-items-center niuu-justify-between niuu-pb-4 niuu-border-b niuu-border-zinc-800 niuu-mb-6`. Left side: cluster name (`niuu-text-xl niuu-font-semibold niuu-text-zinc-100`), followed by inline badges for kind and realm using `niuu-text-xs niuu-px-2 niuu-py-0.5 niuu-rounded niuu-bg-zinc-800 niuu-text-zinc-400 niuu-ml-2`. Status dot: `niuu-w-2 niuu-h-2 niuu-rounded-full` with colour based on status (green=healthy, amber=degraded, red=unhealthy). Right side: action buttons using `@niuulabs/ui` `Button` with appropriate variants.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ClustersPage.tsx`
- `packages/plugin-volundr/src/ui/components/ClusterDetailHeader.tsx` (new)

### 2. Disk resource panel

**Web2 spec**: The cluster detail view has a resources section with three panels: CPU, Memory, and Disk. Each panel shows a circular or bar gauge with current/total values and percentage. The Disk panel shows used/total storage in GiB with a segmented bar (system, pods, logs).
**Web-next currently**: The resources section shows CPU and Memory panels but the Disk panel slot is replaced with a pods count widget that doesn't match the web2 design.
**What to do:** Add a `<DiskResourcePanel />` component matching the existing CPU/Memory panel structure. Show a horizontal segmented bar with colour-coded segments: system (`niuu-bg-indigo-500`), pods (`niuu-bg-cyan-500`), logs (`niuu-bg-amber-500`). Below the bar, show used/total in GiB and percentage. Use `niuu-h-2 niuu-rounded-full niuu-overflow-hidden niuu-flex` for the segmented bar container. Add a legend with coloured dots and labels.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ClustersPage.tsx`
- `packages/plugin-volundr/src/ui/components/DiskResourcePanel.tsx` (new)
- `packages/plugin-volundr/src/ui/components/ResourcePanel.tsx` (if shared layout exists)

### 3. Pods panel with real data binding

**Web2 spec**: The pods panel lists all pods in the cluster with: pod name (monospace), status badge, age, CPU/memory usage as small inline bars, and a restart count. Pods are sortable by name/status/age. The panel header shows total pod count.
**Web-next currently**: The pods panel renders 3-4 hardcoded mock pod entries with static data.
**What to do:** Replace mock data with actual pod data from the cluster API response (or the plugin's data layer). Render each pod row with: name (`niuu-font-mono niuu-text-sm niuu-text-zinc-200`), status badge (reuse `@niuulabs/ui` `Badge`), age (`niuu-text-xs niuu-text-zinc-500`), inline CPU/memory micro-bars (`niuu-w-16 niuu-h-1.5 niuu-rounded-full niuu-bg-zinc-800` with inner fill), restart count. Add a sortable table header. Panel header shows "Pods (N)" count.
**Files to modify:**

- `packages/plugin-volundr/src/ui/ClustersPage.tsx`
- `packages/plugin-volundr/src/ui/components/PodsPanel.tsx` (new or existing)
- `packages/plugin-volundr/src/ui/components/PodRow.tsx` (new)

---

## What to keep as-is

| Element                              | Reason                        |
| ------------------------------------ | ----------------------------- |
| Cluster list sidebar layout          | Already matches web2          |
| CPU and Memory resource panels       | Already correct in design     |
| Cluster list status indicators       | Already using correct colours |
| Overall page grid (sidebar + detail) | Already matches web2 layout   |
| Node count display in cluster cards  | Already present               |

## Shared components

| Component             | Source                                                           |
| --------------------- | ---------------------------------------------------------------- |
| `Button`              | `@niuulabs/ui` — use for Cordon/Drain/Forge Here actions         |
| `Badge`               | `@niuulabs/ui` — use for kind/realm/status badges and pod status |
| `ResourceGauge` / bar | Check `@niuulabs/ui`; if absent, plugin-local                    |
| `ClusterDetailHeader` | Plugin-local — Volundr-specific                                  |
| `DiskResourcePanel`   | Plugin-local — specific resource visualisation                   |

## Acceptance criteria

1. Cluster detail header displays cluster name, kind badge, realm badge, and status dot+label
2. Header includes Cordon (warning), Drain (danger), and Forge Here (primary) action buttons
3. Disk resource panel renders a segmented bar with system/pods/logs breakdown and legend
4. Pods panel displays real pod data (not mock) with name, status, age, resource bars, and restart count
5. Pods panel header shows total pod count and columns are sortable
6. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
7. Visual test passes with <=5% pixel diff against web2 baseline
8. No regressions to existing passing visual tests
