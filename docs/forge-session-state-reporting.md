# Forge Session State & Reporting — Analysis and Design

> Branch: `lexi/forge-state-reporting` (worktree off `lexi/dev-api-integration`).
> Status: **implemented** across the backend, eventing, push, and web layers.

## Implementation status

| Commit | What it does |
|---|---|
| `feat(session): persist activity state and add awaiting_input attention state` | Fixes the activity_state persistence bug; adds the `awaiting_input` state + `needs_attention` on REST. |
| `feat(session): emit needs-input as a first-class realtime and bus event` | `EventType.SESSION_NEEDS_INPUT` + Sleipnir `volundr.session.needs_input` (urgency 0.9); emitted on transition into awaiting_input. |
| `feat(skuld): report awaiting_input on human gates and add a progress heartbeat` | Skuld reports awaiting_input on AskUserQuestion / permission gates; config-driven progress heartbeat keeps long/blocked turns alive and "progressing". |
| `feat(volundr): push notifications for sessions that need attention` | Device-token registry + REST; APNs / webhook / logging channels; AttentionNotifier fired from the session service. |
| `feat(web): render progressing and needs-attention session states` | `awaiting_input` badge/group/panel, live SSE activity handling. |

**Known follow-up:** the in-chat AskUserQuestion answer panel (rendering and
answering the question inside the live session view) is not built — this work
covers session-level state *reporting and visibility*, not the interactive
answer UI. The broker already forwards the `ask_user_question` frame to the
browser; a panel analogous to `PermissionApprovalPanel` would complete it.

## 1. Goal

Make the *runtime state* of every Forge session **observable, push-able, and
actionable**, so that:

- A client (web UI, future iOS app/widget) can clearly tell whether a session is
  **idle**, **progressing/working**, or **blocked needing the user**.
- A session that needs the user (an `AskUserQuestion`, a confirmation, a
  permission) emits a **strong, first-class "needs-attention" signal** that
  propagates all the way to the surface — even producing an APN push and showing
  in an iOS widget when notifications are disabled.
- The state model is a clean, single source of truth that every transport
  (REST snapshot, SSE, Sleipnir bus, notifications) reports consistently.

## 2. Current state model (what exists today)

A session carries **two orthogonal axes**:

| Axis | Type | Values | File |
|---|---|---|---|
| Lifecycle `status` | `SessionStatus` | `created · starting · provisioning · running · stopping · stopped · failed · archived` | `src/volundr/domain/models.py:115` |
| Activity `activity_state` | `SessionActivityState` | `active · idle · tool_executing` | `src/volundr/domain/models.py:128` |

- **Lifecycle** is a real, guarded state machine (`SessionService`,
  `src/volundr/domain/services/session.py`) — "is the container up". `RUNNING`
  means *the pod is alive*, not *the agent is thinking*.
- **Activity** is the "what is the agent doing right now" axis, reported by Skuld
  via `POST /api/v1/forge/sessions/{id}/activity` → `update_activity`
  (`session.py:343`). It is the closest thing we have to "progressing".

### How activity is reported by Skuld (event-driven, no heartbeat)

`_report_activity_state` (`src/skuld/broker.py:4689`) is the only primitive. It is
**debounced** (no re-POST if state unchanged + no metadata) and fired purely off
CLI frames:

| CLI frame | State reported | broker ref |
|---|---|---|
| `assistant` | `active` | `broker.py:3007` |
| `assistant` w/ tool_use | `tool_executing` | `broker.py:3097` |
| `result` (end of turn) | `idle` | `broker.py:3033` |

There is **no periodic "still working" heartbeat**. The only liveness signal is
`last_active`, advanced on every activity report (commit `c1345736`).

## 3. The gaps (why state reporting is "not correct")

1. **`activity_state` / `activity_metadata` are NOT persisted.** The columns
   exist (`migrations/000023_session_activity_state.up.sql`) but the Postgres
   repository never reads or writes them — `create`, `update`, and
   `_row_to_session` all omit them (`src/volundr/adapters/outbound/postgres.py`).
   `update_activity` sets the value in memory and on the live SSE event, but the
   next DB read returns `activity_state: null`. **A `GET /sessions/{id}` or any
   second instance / restart sees no activity state.** Masked in tests by the
   in-memory repo that stores the object by reference (`tests/conftest.py:44`).
   → *Any new state we add inherits this bug until the repo is fixed.*

2. **No "needs-input / needs-attention / blocked / waiting-for-human" state
   exists.** Not in `SessionStatus`, not in `SessionActivityState`. The only
   trace is an untyped `activity_metadata.help_needed` blob that Skuld smuggles
   through with `state="idle"` (`broker.py:2401`) — semantically a session that
   *looks idle but is actually blocked*. And it only fires for **flock-peer /
   workflow-gate** cases, never for a solo session's `AskUserQuestion` or
   permission prompt.

3. **`AskUserQuestion` and permission prompts emit NO state signal.** When a
   solo session blocks waiting for a human answer (`sdk.py:523`
   `_handle_ask_user_question` → `await fut`), `_report_activity_state` is never
   called. The session stays pinned at `active`/`tool_executing`,
   indistinguishable from one that is genuinely computing. The platform learns
   nothing through the state channel — the only durable trace is the raw
   `ask_user_question` frame in the event log, which nobody parses for state.

4. **No "progressing" heartbeat for long turns.** Because reporting is
   event-driven + debounced, a long tool run or long thinking pass produces no
   further activity report. A busy session can look frozen.

5. **Transport drop-offs.**
   - The dedicated `session_activity` SSE event is **not forwarded to Sleipnir**
     (`broadcaster.py:26` map omits it), so cross-service consumers only get
     activity if it rides on `session_updated`.
   - The web client **does not handle `session_activity` at all** — it is
     silently dropped (`web-next/.../adapters/http.ts:1354`). The primary
     session list is **5s HTTP polling**, not SSE-driven.

6. **Client collapses the activity axis.** `toSessionState`
   (`web-next/apps/niuu/src/services.ts:650`) maps both `active` and
   `tool_executing` → `running`, and `idle` → `idle`. `tool_executing` is
   visually indistinguishable from idle-but-running. The "Needs attention" panel
   (`ForgePage.tsx:650`) is **`state === 'failed'` only** — an error strip, not a
   waiting-for-human concept. `ask_user_question` frames are **unhandled** in the
   chat client (`useSkuldChat.ts`).

7. **No push / mobile infrastructure at all.** APNS / FCM / VAPID / Web-Push:
   **zero** matches repo-wide. No iOS / Swift / widget / PWA code. There is
   **no device-token storage**. This is greenfield.

## 4. Assets we can build on

- **Sleipnir registry already has the right shapes** — `ravn.decision.required`
  (urgency ≥ 0.8), `ting.run.needs_approval`, an `urgency` field on every event
  (`src/sleipnir/domain/registry.py`, `domain/events.py`). What's missing is a
  `volundr.session.*` "needs input" type and the SSE `EventType` to match.
- **`ting` has a real notification backbone** — `NotificationService` +
  `NotificationChannel` port + **Telegram** and **outbound-webhook** adapters,
  resolved per-user via the dynamic-adapter `NotificationChannelFactory`, with a
  `notification_subscriptions` table (`src/ting/...`). Extend this with an
  APNS/FCM channel + device-token column rather than rebuilding.
  ⚠️ `ting` must never import `volundr` — the bridge is the **Sleipnir bus**.
- **Durable event log + replay-as-live WS** (on `lexi/forge-replay-as-live`) is
  the only persisted, cursor-resumable channel. A "needs-attention" state frame
  could be made resumable through it later.

## 5. Proposed design

### 5.1 State model — make activity the canonical "what's it doing" axis

Extend `SessionActivityState` so it can express *progressing* and *blocked*:

```
idle            # turn finished; waiting for the user's next message
working         # agent is thinking / streaming (rename/alias of `active`)
tool_executing  # agent is running a tool
awaiting_input  # NEW — blocked on a human: question / confirmation / permission
```

`awaiting_input` is the **needs-attention** state. `activity_metadata` carries the
specifics: `{ kind: "question" | "confirmation" | "permission", request_id,
prompt, options, urgency }`.

Rationale: keep lifecycle (`status`) for "is the pod up" and put the
running/blocked semantics on the activity axis, which already flows end-to-end.

### 5.2 Fix persistence (prerequisite)

Make `PostgresSessionRepository` read & write `activity_state` /
`activity_metadata` (INSERT, UPDATE, `_row_to_session`). Without this, every new
state is dropped on read and the iOS widget (which *queries* state) sees nothing.

### 5.3 Skuld — emit the blocked state + a progress heartbeat

- In the `AskUserQuestion` / permission transports (`sdk.py:523`,
  `persistent_subprocess.py`, broker `_track_pending_permission_request`
  `broker.py:2794`): when the future is created, call
  `_report_activity_state("awaiting_input", extra_metadata={kind, request_id,
  prompt, options})`; clear it (back to `working`/`idle`) on answer/resolve.
- Add a **config-driven progress heartbeat** (interval from `skuld.config`, per
  the no-magic-numbers rule) that re-reports `working`/`tool_executing` with a
  monotonically advancing metadata counter while a turn is in flight, so long
  turns don't look frozen. (Bypasses the debounce via changing metadata.)

### 5.4 Volundr — first-class event + bus forwarding

- Add `EventType.SESSION_NEEDS_INPUT` (or fold richly into `session_activity` /
  `session_updated`, which already carry `activity_state` since `c1345736`).
- Add a Sleipnir registry constant `volundr.session.needs_input` (urgency high)
  + a typed factory in `catalog.py`; **fix the broadcaster map** so activity /
  needs-input events forward to the bus.
- Ensure `GET /sessions` and `GET /sessions/{id}` return the (now-persisted)
  `activity_state` so a polling client / widget can read it.

### 5.5 Notifications & iOS (greenfield, phased)

- A **push fan-out consumer** subscribes to `volundr.session.needs_input` on the
  Sleipnir bus and maps it to a `Notification` dispatched through the existing
  channel pattern. New work: an **APNS channel adapter** + **device-token store**
  (extend `notification_subscriptions`) + a **device-registration endpoint**.
- The **iOS widget** reads session state two ways: (a) APN push for active alert,
  and (b) **polling `GET /sessions?active=true`** so it can show a "needs input"
  badge even with notifications disabled — which is exactly why §5.2 (persist
  activity_state) is non-negotiable.

### 5.6 Web client

- Handle `session_activity` SSE (today dropped); derive `awaiting_input` and a
  distinct `working`/`tool_executing` in `toSessionState`.
- Add an `awaiting_input` lifecycle render (strong amber + pulse) to
  `LifecycleBadge` / `StateDot`, a dedicated "Needs attention" group/filter in
  the session list, and a session-level badge (not just inside the open chat).
- Handle `ask_user_question` frames and render a prompt panel (analogous to the
  existing `PermissionApprovalPanel`).

## 6. Suggested phasing

1. **Foundation (backend, no UI):** persist activity_state (§5.2); extend the
   enum with `awaiting_input` (§5.1); Skuld emits blocked state on
   ask/permission + progress heartbeat (§5.3); event type + Sleipnir
   forwarding + REST exposure (§5.4). *Outcome: the platform correctly reports
   progressing vs blocked, queryable and streamable.*
2. **Web surface:** SSE handling + badge + needs-attention list + ask-user panel
   (§5.6). *Outcome: the existing UI clearly shows the new states.*
3. **Push + iOS:** notification consumer + APNS channel + device-token store +
   registration endpoint + widget contract (§5.5). *Outcome: strong alerts to
   mobile, widget shows needs-input without notifications enabled.*

## 7. Key file index

- State model & service: `src/volundr/domain/models.py:115,128`,
  `src/volundr/domain/services/session.py:343`
- Persistence gap: `src/volundr/adapters/outbound/postgres.py`,
  `migrations/000023_session_activity_state.up.sql`
- Transport: `src/volundr/adapters/outbound/broadcaster.py`,
  `src/volundr/adapters/inbound/rest.py:1222` (SSE), `:1660` (activity report)
- Skuld signaling: `src/skuld/broker.py:4689` (`_report_activity_state`),
  `:2794` (permissions), `src/skuld/transports/sdk.py:523` (`AskUserQuestion`)
- Sleipnir registry: `src/sleipnir/domain/registry.py`, `domain/catalog.py`
- Notifications: `src/ting/domain/services/notification.py`,
  `src/ting/ports/notification_channel.py`, `src/ting/adapters/*notification*.py`
- Web client: `web-next/packages/plugin-volundr/src/adapters/http.ts`,
  `web-next/apps/niuu/src/services.ts:643`,
  `web-next/packages/ui/src/composites/LifecycleBadge/LifecycleBadge.tsx`,
  `web-next/packages/plugin-volundr/src/ui/hooks/useSkuldChat.ts`
</content>
</invoke>
