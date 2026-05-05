# Sessions — Visual Parity with web2

**Visual test:** `e2e/visual/ravn.visual.spec.ts` → `ravn sessions matches web2`
**Status:** FAIL (missing header card, missing message filter toolbar, missing composer, missing message type styling for think/tool/emit, simplified timeline, missing injects/emissions in sidebar)
**Web2 baseline:** `e2e/__screenshots__/web2/ravn/sessions.png`
**Web2 source:** `web2/niuu_handoff/ravn/design/sessions.jsx`
**Web-next source:** `packages/plugin-ravn/src/ui/SessionsView.tsx`, `packages/plugin-ravn/src/ui/MessageRow.tsx`, `packages/plugin-ravn/src/ui/ActiveCursor.tsx`

---

## Summary

The Sessions view has the correct 3-pane layout (session list, transcript center, context sidebar) but is missing significant UI features from web2: a rich header card with metadata and action buttons, a message filter toolbar, a chat composer, full timeline with intermediate events, and injects/emissions sections in the context sidebar. The MessageRow component exists with correct kind-based styling but the overall transcript experience is incomplete.

---

## Required changes

### 1. Add transcript header card

**Web2 spec** (sessions.jsx): Transcript area has a header card at the top containing: persona avatar (circle + letter), session title, status badge (running/idle/stopped/failed), metadata row (model, created time, duration), metrics row (messages, tokens, cost), and action buttons (export, pause, abort).
**Web-next currently**: Transcript has a simple header div with just persona name, model, and message count text.
**What to do:**

1. Create a `TranscriptHeader` component with:
   - `PersonaAvatar` (from `@niuulabs/ui`) showing persona letter and role color.
   - Session title (or fallback `Session {id.slice(0,8)}`).
   - Status badge using `StateDot` + text.
   - Metadata row: model (monospace), created time, duration (computed from createdAt to now or end time).
   - Metrics row: message count, token count, cost.
   - Action buttons group: Export (download icon), Pause (pause icon, disabled if not running), Abort (stop icon, disabled if not running).
2. Style with `.rv-transcript-header` classes matching web2 card appearance (subtle border-bottom, padding, flex layout).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/SessionsView.tsx`
- `packages/plugin-ravn/src/ui/SessionsView.css` (or co-located CSS)

### 2. Add message filter toolbar

**Web2 spec** (sessions.jsx): Below the header, a toolbar with a segmented control allowing filtering messages by kind: All | User | Assistant | Tool | Emit | System | Think. Active segment is highlighted. Changing filter updates the visible messages in the transcript.
**Web-next currently**: No filter toolbar exists. All messages are shown unfiltered.
**What to do:**

1. Add a `FilterToolbar` component with segmented buttons for each message kind.
2. Track filter state in `Transcript` component.
3. Filter the messages array before rendering, keeping "All" as default.
4. Style as a horizontal bar below the header with pill-shaped segmented buttons.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/SessionsView.tsx`
- `packages/plugin-ravn/src/ui/SessionsView.css`

### 3. Add chat composer

**Web2 spec** (sessions.jsx): At the bottom of the transcript pane, a composer bar with: multi-line textarea (auto-resize, placeholder "Send a message..."), send button (arrow icon), and a subtle toolbar row above with inject/attach buttons. Composer is disabled when session is not running.
**Web-next currently**: No composer exists. The transcript ends at the bottom of the message list with the `ActiveCursor`.
**What to do:**

1. Add a `Composer` component below the transcript body (above `ActiveCursor` or replacing it when session is running).
2. Textarea with auto-resize, placeholder text, and disabled state.
3. Send button that dispatches a send action (wire to port later).
4. Show inject/attach controls as icon buttons in a toolbar row above the textarea.
5. Disable entire composer when `session.status !== 'running'`.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/SessionsView.tsx`
- `packages/plugin-ravn/src/ui/SessionsView.css`
- `packages/plugin-ravn/src/ports/index.ts` (add sendMessage to session port if missing)

### 4. Enrich context sidebar — add injects and emissions sections

**Web2 spec** (sessions.jsx ContextSidebar): Sidebar has 6 sections: Summary, Timeline (full with intermediate events), Stats, Injects (list of injected context items with source and timestamp), Emissions (list of emitted events with type, timestamp, payload preview), and Raven card.
**Web-next currently**: Sidebar has 4 sections: Summary, Timeline (only start + end), Stats, and Raven card. Missing Injects and Emissions entirely.
**What to do:**

1. Add "Injects" section between Timeline and Stats:
   - Query session injects (items injected into context during the session).
   - Each item: source label, timestamp, truncated content preview.
   - Styled as `.rv-ctx-injects` list.
2. Add "Emissions" section between Stats and Raven card:
   - Query session emissions (events emitted during the session).
   - Each item: event type badge (cyan), timestamp, payload preview (monospace, truncated).
   - Styled as `.rv-ctx-emissions` list.
3. Enrich Timeline to show intermediate events (tool calls, emits, errors) not just start/end.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/SessionsView.tsx` (ContextSidebar)
- `packages/plugin-ravn/src/ui/SessionsView.css`
- `packages/plugin-ravn/src/ui/hooks/useSessions.ts` (add injects/emissions queries if needed)
- `packages/plugin-ravn/src/domain/session.ts` (add injects/emissions to model if missing)

### 5. Enrich timeline with intermediate events

**Web2 spec** (sessions.jsx Timeline): Timeline shows all significant events: session start, each tool call (with tool name), each emit (with event type), errors/failures, and session end. Events have colored dots matching their kind.
**Web-next currently**: Timeline shows only "started" and the final status (stopped/failed/idle). No intermediate events.
**What to do:**

1. Derive timeline events from the messages list (tool_call, emit, and error messages become timeline entries).
2. Render each with a kind-colored dot (amber for tool, cyan for emit, red for error).
3. Include timestamp and a short label (tool name or event type).
4. Cap at ~15 entries with a "show more" expand control.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/SessionsView.tsx` (ContextSidebar Timeline section)
- `packages/plugin-ravn/src/ui/SessionsView.css`

---

## Shared components

- `StateDot`, `PersonaAvatar` — from `@niuulabs/ui`
- `MessageRow` — plugin-local (already has correct kind styling, no changes needed)
- `ActiveCursor` — plugin-local (existing, may need adjustment for composer integration)
- Transcript header, filter toolbar, composer — plugin-local (new)

## Acceptance criteria

1. Transcript header card shows persona avatar, title, status badge, metadata (model, time, duration), metrics (messages, tokens, cost), and action buttons (export, pause, abort).
2. Message filter toolbar renders segmented control for All/User/Assistant/Tool/Emit/System/Think with working filtering.
3. Chat composer appears at bottom of transcript with auto-resize textarea, send button, and disabled state when session is not running.
4. Context sidebar includes Injects section showing injected context items with source and timestamp.
5. Context sidebar includes Emissions section showing emitted events with type badge and payload preview.
6. Timeline shows intermediate events (tool calls, emits, errors) with kind-colored dots, not just start and end.
7. Visual regression test `ravn sessions matches web2` passes within acceptable diff threshold.
8. Unit tests cover header card, filter toolbar, composer, injects, emissions, and enriched timeline.
9. Coverage remains at or above 85%.
