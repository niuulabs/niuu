# Ravens — Visual Parity with web2

**Visual test:** `e2e/visual/ravn.visual.spec.ts` → `ravn ravens split view matches web2`
**Status:** FAIL (detail pane missing 4 of 5 tabs content sections, missing mounts/write-routing/cascade/deployment fields in overview)
**Web2 baseline:** `e2e/__screenshots__/web2/ravn/ravens-split.png`
**Web2 source:** `web2/niuu_handoff/ravn/design/pages.jsx`, `web2/niuu_handoff/ravn/design/data.jsx`
**Web-next source:** `packages/plugin-ravn/src/ui/RavensPage.tsx`, `packages/plugin-ravn/src/ui/RavnDetail.tsx`, `packages/plugin-ravn/src/ui/RavnDetail.css`

---

## Summary

The Ravens page layout (3 modes: split/table/cards, 420px list panel) matches web2 structurally, but the detail pane is significantly simplified. Web2 has a rich 5-tab detail panel (overview, triggers, activity, sessions, connectivity) with full content in each tab. Web-next has the 5 tabs implemented but the content within each is minimal compared to web2's trigger cards, full activity log, session cards with metrics, and connectivity panels (MCP servers, gateway channels, event subscriptions).

---

## Required changes

### 1. Enrich Overview tab — add identity/runtime/mounts panels

**Web2 spec** (pages.jsx RavnDetail): Overview tab contains three distinct panels: (a) Identity panel with avatar, persona name, role badge, summary text; (b) Runtime panel with model, uptime, status, location, iteration budget, write routing selector; (c) Mounts panel showing attached mounts with MountChip icons and read/write badges.
**Web-next currently**: Has a flat `<dl>` with persona, state, model, since, location, and a budget bar. No mounts panel, no runtime panel separation, no write routing, no iteration budget display.
**What to do:**

1. Restructure `OverviewSection` into three sub-panels: Identity, Runtime, Mounts.
2. Identity: Add `PersonaAvatar`, role badge, summary text.
3. Runtime: Add iteration budget display, write routing indicator (using `MountChip`), cascade/deployment fields.
4. Mounts: Query ravn mounts and display each with `MountChip` + read/write indicator.
5. Keep danger zone (suspend/delete) at the bottom.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/RavnDetail.tsx` (OverviewSection)
- `packages/plugin-ravn/src/ui/RavnDetail.css`
- `packages/plugin-ravn/src/domain/ravn.ts` (add mounts, iterationBudget, writeRouting fields if missing)
- `packages/plugin-ravn/src/ports/index.ts` (if service needs mount query)

### 2. Enrich Triggers tab — add trigger cards with full metadata

**Web2 spec** (pages.jsx TriggersSection): Each trigger renders as a card showing: kind icon, spec (cron expression or event pattern), enabled/disabled toggle, last-fired timestamp, fire count, linked persona. Cards have a subtle border and hover state.
**Web-next currently**: Renders a flat `<ul>` with `<li>` items showing kind, spec, and on/off text. No card styling, no timestamps, no fire count, no toggle.
**What to do:**

1. Restyle each trigger as a card (`.rv-trigger-card`) with border, padding, and hover.
2. Add fields: `lastFiredAt`, `fireCount` from the trigger domain model.
3. Add an enabled/disabled toggle switch (visual only for now, wire action later).
4. Show linked persona name with avatar.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/RavnDetail.tsx` (TriggersSection)
- `packages/plugin-ravn/src/ui/RavnDetail.css`
- `packages/plugin-ravn/src/domain/trigger.ts` (add lastFiredAt, fireCount if missing)

### 3. Enrich Activity tab — full scrollable activity log

**Web2 spec** (pages.jsx ActivitySection): Shows a full scrollable activity log for the selected ravn with rows: timestamp, kind badge (colored), message excerpt, expandable detail. Supports filtering by kind. Shows a "live" indicator when ravn is active.
**Web-next currently**: Shows only the last 10 messages from one session, with minimal styling (`[kind] content`).
**What to do:**

1. Aggregate activity across all sessions for this ravn, not just one.
2. Add kind-colored badges matching MessageRow styling (user=indigo, asst=neutral, tool=amber, emit=cyan, think=purple, system=muted).
3. Add a filter control for kind (segmented control or checkbox group).
4. Add a "live" pulsing indicator when `ravn.status === 'active'`.
5. Make the log scrollable with virtualization if list exceeds 100 items.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/RavnDetail.tsx` (ActivitySection)
- `packages/plugin-ravn/src/ui/RavnDetail.css`
- `packages/plugin-ravn/src/ui/hooks/useSessions.ts` (if multi-session aggregation needed)

### 4. Enrich Sessions tab — session cards with metrics

**Web2 spec** (pages.jsx SessionsSection): Each session renders as a card with: status dot, persona name, model, created timestamp, message count, cost, duration, and a mini budget bar. Cards are clickable (navigate to Sessions view with that session selected).
**Web-next currently**: A flat `<ul>` with `<li>` items showing only status dot, truncated ID, and status text.
**What to do:**

1. Render each session as a card (`.rv-session-card`) with persona name, model, timestamp.
2. Add metrics row: message count, cost ($X.XX), duration.
3. Add a mini BudgetBar if cost/cap data is available.
4. Make cards clickable — dispatch `ravn:session-selected` event to navigate.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/RavnDetail.tsx` (SessionsSection)
- `packages/plugin-ravn/src/ui/RavnDetail.css`

### 5. Enrich Connectivity tab — MCP servers, gateway channels, event subscriptions

**Web2 spec** (pages.jsx ConnectivitySection): Three sub-panels: (a) MCP Servers — list of connected MCP server names with status dots; (b) Gateway Channels — list of channels this ravn communicates through; (c) Event Subscriptions — list of consumed/produced events with arrows showing direction.
**Web-next currently**: Shows only the model name and a text note saying "View the Events tab for the full graph."
**What to do:**

1. Add MCP servers sub-panel querying ravn connectivity data.
2. Add gateway channels sub-panel.
3. Add event subscriptions sub-panel showing consumed/produced events with direction indicators.
4. Each sub-panel uses a consistent card style with `.rv-conn-panel` class.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/RavnDetail.tsx` (ConnectivitySection)
- `packages/plugin-ravn/src/ui/RavnDetail.css`
- `packages/plugin-ravn/src/domain/ravn.ts` (add mcpServers, channels fields if missing)
- `packages/plugin-ravn/src/ports/index.ts` (connectivity query if needed)

---

## Shared components

- `StateDot`, `BudgetBar`, `PersonaAvatar`, `MountChip` — from `@niuulabs/ui`
- `RavnDetail` with 5-tab pane — plugin-local (ravn only)
- `RavnListRow`, `RavnCard`, `RavnTableRow` — plugin-local (already exist, no changes)

## Acceptance criteria

1. Detail pane Overview tab shows three distinct panels: Identity (avatar, role, summary), Runtime (model, uptime, budget, write routing), Mounts (list with MountChip).
2. Triggers tab renders each trigger as a styled card with kind icon, spec, toggle, last-fired, fire count.
3. Activity tab shows aggregated activity across all ravn sessions with kind-colored badges and filtering.
4. Sessions tab renders session cards with metrics (messages, cost, duration) and mini budget bars.
5. Connectivity tab shows MCP servers, gateway channels, and event subscriptions in separate panels.
6. Visual regression test `ravn ravens split view matches web2` passes within acceptable diff threshold.
7. Unit tests cover each tab section component with loading, empty, and populated states.
8. Coverage remains at or above 85%.
