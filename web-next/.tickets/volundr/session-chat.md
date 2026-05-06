# Session Chat — Visual Parity with web2

**Visual test:** `e2e/visual/volundr.visual.spec.ts` → `session-chat`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/volundr/session-chat.png`
**Web2 source:** `web2/niuu_handoff/volundr/design/sessions.jsx`
**Web-next source:** `packages/plugin-volundr/src/ui/SessionDetailPage.tsx`

---

## Summary

The Session Chat detail page is at 90% parity but is missing the peer gateway display in the sidebar, file change summary counts in the header, a disk meter in the resources panel, and the diffs/chronicle/logs tabs are empty placeholders with no content structure.

---

## Required changes

### 1. Peer gateway display in sidebar

**Web2 spec**: The right sidebar of the session detail view includes a "Gateway" section showing the peer gateway name, region, and connection latency. It appears between the "Model" and "Resources" sections. The gateway name is displayed in medium weight, region as a muted subtitle, and latency as a coloured value (green <100ms, amber <500ms, red >=500ms).
**Web-next currently**: The sidebar shows Model and Resources sections but has no Gateway section.
**What to do:** Add a `<GatewaySection />` between Model and Resources in the sidebar. Layout: `niuu-py-3 niuu-border-b niuu-border-zinc-800`. Section label: `niuu-text-xs niuu-font-semibold niuu-text-zinc-500 niuu-uppercase niuu-tracking-wider niuu-mb-2`. Gateway name: `niuu-text-sm niuu-font-medium niuu-text-zinc-200`. Region: `niuu-text-xs niuu-text-zinc-500`. Latency: `niuu-text-xs niuu-font-mono` with colour conditional on value — `niuu-text-emerald-400` (<100ms), `niuu-text-amber-400` (<500ms), `niuu-text-red-400` (>=500ms).
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionDetailPage.tsx`
- `packages/plugin-volundr/src/ui/components/GatewaySection.tsx` (new)

### 2. File change summary in header

**Web2 spec**: The session detail header (below the session name and status) shows a compact file change summary: `+3 added`, `~5 modified`, `-1 deleted` using green/amber/red text respectively, separated by spacing. This gives at-a-glance visibility of the session's file impact.
**Web-next currently**: The header shows session name, status badge, duration, and model but no file change summary.
**What to do:** Add a file change stats row below the existing header metadata. Use inline spans: added (`niuu-text-emerald-400 niuu-text-xs`), modified (`niuu-text-amber-400 niuu-text-xs`), deleted (`niuu-text-red-400 niuu-text-xs`), separated by `niuu-mx-2`. Prefix each with its symbol (+, ~, -). Source from `session.fileChanges` or similar field containing `{ added: number, modified: number, deleted: number }`.
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionDetailPage.tsx`
- `packages/plugin-volundr/src/ui/components/FileChangeSummary.tsx` (new)

### 3. Disk meter in resources panel

**Web2 spec**: The resources panel in the sidebar shows three meters: CPU, Memory, and Disk. Each is a small horizontal bar with a percentage label. The Disk meter shows workspace storage consumption for the session's pod.
**Web-next currently**: The resources panel shows CPU and Memory meters but no Disk meter.
**What to do:** Add a Disk meter matching the existing CPU/Memory meter pattern. Render below Memory. Label: "Disk" (`niuu-text-xs niuu-text-zinc-500`). Bar container: `niuu-h-1.5 niuu-rounded-full niuu-bg-zinc-800 niuu-w-full`. Fill: `niuu-h-full niuu-rounded-full niuu-bg-indigo-500` with width as percentage. Value label: `niuu-text-xs niuu-text-zinc-400 niuu-ml-2`. Source from `session.resources.disk` or the pod's disk usage metric.
**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionDetailPage.tsx`
- `packages/plugin-volundr/src/ui/components/ResourcesPanel.tsx` (if exists)

### 4. Diffs/Chronicle/Logs tab content structure

**Web2 spec**: The bottom panel of the session detail has tabs: Chat (active by default), Diffs, Chronicle, and Logs. In web2: (a) Diffs tab shows a file tree on the left and a diff viewer on the right with unified diff rendering (green/red line highlighting), (b) Chronicle tab shows a timeline of events with timestamps, event types, and descriptions, (c) Logs tab shows a terminal-style monospace log viewer with ANSI colour support and auto-scroll.
**Web-next currently**: Diffs, Chronicle, and Logs tabs exist in the tab bar but render empty placeholder divs or "Coming soon" text.
**What to do:** Add structural scaffolding (not full implementation) for each tab:

- **Diffs**: Two-pane layout (`niuu-flex niuu-h-full`). Left file tree (`niuu-w-48 niuu-border-r niuu-border-zinc-800 niuu-overflow-y-auto`) with placeholder file entries. Right diff area (`niuu-flex-1 niuu-overflow-y-auto niuu-font-mono niuu-text-xs`) with sample diff line styling (added: `niuu-bg-emerald-500/10`, removed: `niuu-bg-red-500/10`).
- **Chronicle**: Vertical timeline (`niuu-space-y-3 niuu-pl-4 niuu-border-l niuu-border-zinc-800`). Each event: dot + timestamp + type badge + description.
- **Logs**: Monospace container (`niuu-font-mono niuu-text-xs niuu-bg-zinc-950 niuu-p-4 niuu-rounded-lg niuu-overflow-y-auto niuu-h-full niuu-text-zinc-300`). Render available log lines or an empty state.

**Files to modify:**

- `packages/plugin-volundr/src/ui/SessionDetailPage.tsx`
- `packages/plugin-volundr/src/ui/components/DiffsTab.tsx` (new)
- `packages/plugin-volundr/src/ui/components/ChronicleTab.tsx` (new)
- `packages/plugin-volundr/src/ui/components/LogsTab.tsx` (new)

---

## What to keep as-is

| Element                                         | Reason               |
| ----------------------------------------------- | -------------------- |
| Chat message rendering (user/assistant bubbles) | Already matches web2 |
| Tab bar design and switching behaviour          | Already correct      |
| Sidebar overall layout and width                | Already matches web2 |
| Session status badge in header                  | Already correct      |
| Model display in sidebar                        | Already correct      |
| Message input area at bottom                    | Already matches web2 |

## Shared components

| Component           | Source                                                              |
| ------------------- | ------------------------------------------------------------------- |
| `Badge`             | `@niuulabs/ui` — for event type badges in Chronicle                 |
| `Tabs`              | `@niuulabs/ui` — already in use for the tab bar                     |
| `ResourceMeter`     | Plugin-local or check `@niuulabs/ui` for a progress/meter component |
| `GatewaySection`    | Plugin-local — Volundr-specific                                     |
| `FileChangeSummary` | Plugin-local — Volundr-specific                                     |
| `DiffViewer`        | Plugin-local — may later extract to shared if other plugins need it |

## Acceptance criteria

1. Sidebar displays a Gateway section with name, region, and latency (colour-coded by threshold)
2. Session header shows file change summary: added (green), modified (amber), deleted (red) counts
3. Resources panel includes a Disk meter with the same visual style as CPU/Memory
4. Diffs tab renders a two-pane layout with file tree and diff viewer structure
5. Chronicle tab renders a vertical timeline with event entries
6. Logs tab renders a monospace terminal-style log container
7. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
8. Visual test passes with <=5% pixel diff against web2 baseline
9. No regressions to existing passing visual tests
