# Session notifications and the Forge MCP: implementation plan

Status: plan, 2026-09-23. Branch `forge/session-notifications`, cut from the served
release `forge-opus55-20260923` (`636017fb` + the Opus 5.5 effort patch).
This is the implementation companion to
[mcp-skills-notifications-design.md](mcp-skills-notifications-design.md). It keeps
that design's contract (typed events, idempotent submit, cursor feeds, no external
delivery by default, honest delivery states) and pins down *where* each piece goes
in today's code. The main deviations are in the decisions below.

## Goal

A coding session (Claude Code or Codex under Skuld) can call `forge_notify`
through an automatically injected **Forge MCP**. It can use the same MCP to see
and supervise other sessions. Forge turns each notification into a durable,
cursor-ordered event. That event:

1. appears inline in the session's conversation, including history and replay,
2. appears in a live **Notifications** feed across every session and host, with
   filters by kind, severity, source, host, project and session,
3. can be delivered to Telegram, push or a webhook through filter rules and
   sink adapters, with a durable outbox.

## Architecture in one picture

```
 session (Claude / Codex)                      Skuld broker (per session, loopback)
 ┌──────────────────────┐  stdio JSON-RPC     ┌─────────────────────────────────────┐
 │ model ── forge MCP ──┼──► skuld.forge_mcp ─┼─► /api/forge-mcp/*  (launch secret) │
 └──────────────────────┘   (spawned by CLI)  │   notify → _emit_broker_frame       │
                                              │     (log first, then broadcast)     │
                                              │   read/peer tools → Forge REST      │
                                              │     via _get_http_client (scoped)   │
                                              └───────────────┬─────────────────────┘
                                   POST /sessions/{id}/log    │ (existing buffered,
                                                              ▼  retried, seq-resumed)
 Forge (volundr) ───────────────────────────────────────────────────────────────────
  session_event_log INSERT ──same txn──► sessions.latest_final_seq     (exists: inbox)
         └─ projection (before ack) ──► forge_notifications (seq BIGSERIAL)  NEW
                               same txn └► forge_notification_deliveries (outbox) NEW
  after commit: broadcaster.publish(SESSION_NOTIFICATION) ─► /sessions/stream (SSE)
                                                          └► Sleipnir (optional)
  NotificationDispatcher: claim outbox (SKIP LOCKED) ─► niuu NotificationSink
                                                         ├ Telegram (moved from Ting)
                                                         ├ Webhook  (moved from Ting)
                                                         └ Push/APNs (wraps volundr)
 Guild facade (niuu): /sessions/stream passes all event types through + tags instance
                      /notifications fan-out with per-node cursors              NEW
 web-next (plugin-volundr): inline NotificationCard · Notifications page · badge
```

## Key decisions

### 1. One write path: notifications are entries in the durable session log

The broker emits a notification the way `present-file` emits its card
(`src/skuld/broker_api.py:759`). It builds a `conversation.turn` and appends it
with `broker._append_turn`, which writes the durable log, and then broadcasts it.
That is the log-first rule `_emit_broker_frame` enforces (`event_log.py:414`). Forge
**projects** the stored entry into `forge_notifications`, together with its outbox
rows, in one transaction. That happens after the idempotent log insert and before
the append is acknowledged, so a producer retry re-runs the projection without
creating duplicates. It mirrors how the per-reader inbox is derived from the same
insert (`src/volundr/adapters/outbound/pg_session_event_log.py:35`, which updates
`sessions.latest_final_seq`). The contract (§3) has the exact rules.

What this gets us for free:
- **Exact inline position.** The card sits at its `seq` in the transcript. History
  paging, replay, the replay WebSocket and older clients all see it.
- **Offline behaviour.** Skuld's log already buffers, retries, reports `log_gap`
  and resumes its sequence from `/log/head`. While Forge is down a notification is
  *pending locally* and commits later, as the design requires.
- **Idempotency.** Log inserts are `ON CONFLICT (session_id, seq) DO NOTHING`. The
  notification id is a uuid5 of `(session, turn id, kind)`, so producer retries
  never duplicate.
- **Unforgeable provenance.** The broker *is* the session. A model cannot claim to
  be another session.

Turn shape: role `assistant`, `visibility: public`, `metadata.kind = "notification"`,
and one `tool_use` part named `forge_notification`, the same pattern as the
`present_file` part. It carries `{notification_id, kind, severity, title, body,
links[]}`. Older web and iOS clients degrade to a generic tool card instead of
breaking. It never sets `final_output`, so it does not mark the session unread.

### 2. Automatic end-of-turn "reply ready" comes from the server, not the model

The same insert already detects a final assistant output to maintain
`latest_final_seq`. When that projection fires, also insert a `reply_ready`
notification (`source = system`) anchored to that seq, with the first ~280
characters of the reply as its body. Because this lives in the server, it works
for every engine, including OpenCode and Muse, which have no MCP. It needs no
broker change and cannot be forgotten by a model. It is not rendered inline,
because it *is* the final message. It is visible in the feed, with external
delivery off unless a rule enables it.

Likewise `update_activity` → `SESSION_NEEDS_INPUT`
(`src/volundr/domain/services/session.py:~520`) produces an `attention`
notification. That puts the existing APNs attention push path behind the same
feed and rules.

Kinds: `milestone`, `decision`, `attention`, `reply_ready`, `error`, `info`
(model-callable: milestone / decision / attention / info). Severity: `info`,
`success`, `warning`, `critical`. A final reply is not "task complete".

### 3. The Forge MCP is hosted by the broker and reached through a stdio shim

The design doc proposed a Forge-owned Streamable HTTP endpoint at `/mcp/forge`.
For sessions we recommend **`python -m skuld.forge_mcp`** instead: a stdio MCP
server that each harness spawns, which forwards to its own broker over loopback.
- **Every engine already supports stdio.** Claude tmux takes `--mcp-config`
  (`tmux_interactive.py:2765`), SDK/subprocess take `mcp_servers`, Codex takes
  `-c mcp_servers.forge.*` (`codex_ws.py:549`), and Grok ACP `session/new`
  accepts stdio servers (`grok.py:669` is currently hard-coded to `[]`).
  HTTP MCP would first need the translator fixes listed in P0.
- **No Forge credential enters the session.** The shim only holds a per-launch
  broker secret. The broker holds the Forge credential (`_get_http_client`,
  `event_log.py:111`), following the `/api/message` precedent.
- **`notify` stays local**, which is what gives decision 1 its properties.
- **Guild just works.** Every node's broker talks to its own Forge, and Forge
  routes peer calls through the facade. No MCP routing across nodes is needed.

Injection: Skuld appends a built-in `forge` entry in `_build_transport_kwargs`
(`src/skuld/transport_lifecycle.py:48`), so every engine's existing translator
(`src/skuld/transports/mcp_config.py`) carries it. There is an opt-out setting,
and it is merged by name so a launch spec can override it.

Tool handlers live in a transport-neutral module. Later, Forge can expose the
same handlers as `/mcp/forge` over HTTP for external agents (OpenClaw, Lexi). That
is where the design doc's endpoint belongs.

Tool set, v1. It mirrors the 10 Lexi agent-service Forge tools plus the design doc. The
final names are in [the contract](session-notifications-contract.md): the server is
`forge` and the tools are `notify`, `environment` and so on, so Claude sees them as
`mcp__forge__notify`.

| Tool | v1 access | Backed by |
|---|---|---|
| `forge_environment` | default | broker: node, session, workspace, project, model/engine, runtime version, attached MCPs |
| `forge_notify` | default | broker emits frame, returns `{notification_id, session_seq, state: committed\|pending}` |
| `forge_list_notifications` | default (own project) | `GET /notifications?after=` |
| `forge_list_sessions`, `forge_get_session` | default (own project, read) | `GET /sessions`, `/sessions/{id}` |
| `forge_session_transcript` | default (own project, read) | `GET /sessions/{id}/log?after=` or `/conversation` tail |
| `forge_send_message` | **grant** | `POST /sessions/{id}/messages` + delivery ledger (`request_id`) |
| `forge_create_session`, `forge_start_session`, `forge_stop_session` | **grant** | existing lifecycle routes; no delete |

The model learns when to notify from three places:
- `_FORGE_NOTIFY_INSTRUCTION`, added next to `_PRESENT_FILE_INSTRUCTION` in
  `_composed_system_prompt` (`tmux_interactive.py:2770`).
- The same text in Codex `baseInstructions` (`codex_ws.py:684`). Codex gets no
  present-file text there today.
- Rich tool descriptions.

The guidance: report milestones and decisions, keep it brief, link artifacts,
don't spam progress, and don't claim the whole task is done. A `forge-notify`
skill arrives with the skills-bundle work (design doc step 3).

### 4. Auth: scoped, session-bound, held only by the broker

- **Loopback routes.** A per-launch random secret (a 0600 file under the runtime
  dir; its path is passed as an argument, never the value) guards
  `/api/forge-mcp/*`. It should also guard `/api/present-file` and `/api/message`,
  which are unauthenticated today, while `SkuldSettings.host` defaults to
  `0.0.0.0` (`src/skuld/config.py:692`).
- **Forge credential.** At launch, mint a short-lived **session-bound workload
  token** with the issuer that `openshell_session` uses
  (`WorkloadIdentityService.issue_token`, `niuu/domain/services/workload_identity.py:214`),
  carrying `session_id`, owner and scopes.
  - Add `forge:notify`, `forge:session:read`, `forge:session:message` and
    `forge:session:lifecycle` to `KNOWN_WORKLOAD_SCOPES`.
  - Enforce them with `require_scope` in Volundr and in the Guild facade.
  - Peer mutation needs an operator grant: a launch-spec or project setting.
- **Scrub the session environment.** `claude_env.py:34` and `codex_ws.py:322` copy
  the broker's whole environment into the session, which would leak
  `SKULD__EXTERNAL_API_TOKEN` / `VOLUNDR_EXTERNAL_API_TOKEN`.
- **Threat model, stated honestly.** In local mode every session runs as the same
  Unix user, so this cannot stop a deliberately malicious local process. It does
  stop tool-level spoofing and accidental cross-session actions.

### 5. Storage (migration `000069`, in three places)

Write it in `migrations/`, `charts/volundr/templates/migrations-configmap.yaml`
**and** `src/cli/migrations/volundr/`. See commit `bbe159dd`; the rule file lists
only the first two.

- `forge_notifications`
  - Columns: `id UUID PK` (uuid5), `seq BIGSERIAL UNIQUE` (feed cursor),
    `session_id`, `session_seq`, `owner_id`, `tenant_id`, `project_id NULL`,
    `node_id`, `kind`, `severity`, `source` (agent|system), `title`,
    `body` (markdown, capped), `links JSONB`, `engine`, `model`,
    `correlation_id NULL`, `created_at`.
  - `UNIQUE (session_id, session_seq, kind)`.
  - Indexes: `(owner_id, seq)`, `(session_id, seq)`, `(project_id, seq)`.
  - No FK to sessions, like `session_event_log`, so notifications outlive their
    session. A retention setting is added to config (no magic numbers).
- `forge_notification_read_states`: `(user_id PK, read_through_seq, revision)`, a
  compare-and-swap watermark per reader. It is kept separate from session read-state.
- `forge_notification_rules`: owner, name, enabled, `match JSONB` (kinds,
  min_severity, sources, project/session ids), sink adapter reference (class path
  plus kwargs, or an `integration_connections` id for credentials), quiet hours,
  rate limit.
- `forge_notification_deliveries` (outbox): `(notification_id, rule_id) UNIQUE`,
  sink, status `pending|claimed|delivered|failed|dead`, attempts,
  `next_attempt_at`, `lease_until`, `last_error`. Rules are evaluated inside the
  projection transaction, so *committed ⇒ scheduled*.

### 6. Delivery: shared ports in niuu, the outbox worker in Volundr

- Add `src/niuu/ports/notifications.py`. It defines `NotificationSink.send() ->
  DeliveryResult`, which raises or returns failure and never swallows it, and
  reuses `ChannelResolverPort`.
- Move `TelegramNotificationAdapter` and `WebhookNotificationAdapter` from
  `src/ting/adapters/` to `src/niuu/adapters/notifications/`. Keep re-exports at
  the old paths, because `integration_connections.adapter` stores class paths.
  Volundr may not import Ting.
- Wrap the Volundr push channels (APNs, relay, logging; `push_channels.py`) as one
  sink that resolves devices through `DeviceTokenRepository`.
- Add a Volundr `NotificationDispatcher` background loop. It follows Ting's A2A
  outbox (`postgres_a2a_push.py:119`: `FOR UPDATE SKIP LOCKED`, a lease, and
  exponential backoff).
- Sinks are selected with the dynamic `adapter:` + kwargs rule. There is **no
  external delivery by default**.

### 7. Live streaming and the Guild

- Add `EventType.SESSION_NOTIFICATION = "session_notification"`
  (`src/volundr/domain/models.py:125`) and publish it after commit through
  `InMemoryEventBroadcaster`.
- The facade's `/sessions/stream` already forwards **every** event type and tags
  it with its instance (`src/niuu/adapters/inbound/rest_volundr.py:1118`), so the
  live path needs no facade change.
- Clients resume by cursor over REST: `GET /notifications?after=`. SSE has no
  `id:` / Last-Event-ID today, and this plan does not add a second stream.
- **Filter notification events by principal.** The broadcaster currently sends
  everything to everyone (`broadcaster.py:54`).
- Optionally register `volundr.session.notification` in Sleipnir (`registry.py`,
  `catalog.py`) and map it in `broadcaster.py:26` so Ravn and Ting can react.
- New facade handlers: `GET /notifications` fans out with an opaque cursor per
  node, merges by `(created_at, node, seq)` and reports
  `X-Forge-Unavailable-Instances`. Also add `POST /notifications/read` and
  `GET /sessions/{id}/notifications`, routed to the owning node.

### 8. Web (web-next, plugin-volundr)

- **Inline.** Add a `NotificationCard` beside `PresentedFileCard` in the tool-part
  dispatch (`web-next/packages/ui/src/chat/components/ChatMessages/ChatMessages.tsx:340`).
  Because the notification is a real transcript turn, it avoids two known traps:
  the `conversation_history` wipe and the `isVisibleMessage` filter on system
  messages. Collapse the raw `mcp__forge__forge_notify` call into the card.
- **Notifications page.**
  - Add a port, `INotificationFeed` (`list(filter, cursor)`, `subscribe`,
    `markRead`), plus the http and mock adapters.
  - Add a `session_notification` branch in `ensureStream`
    (`plugin-volundr/src/adapters/http.ts:1488`). Also add
    `session_read_state`, which is currently dropped.
  - Add a `useNotifications(filter)` hook: `useInfiniteQuery` plus a live prepend
    through `setQueryData`, as in `useSessionStore`.
  - The page itself follows the `ActivityPage` / `AuditLogSection` filter UX, using
    FilterBar, Chip, relTime, the empty/error/loading states, and
    `useForgePreference` for saved filters.
  - Add a tab and route `/volundr/notifications` in `plugin-volundr/src/index.ts`,
    and an unread count on the tab through `PluginTab.count`.
- **Tests.** Co-located vitest tests (85% gate) plus a Playwright spec in
  `e2e/`: happy path, loading, error and keyboard, as `web-next/CLAUDE.md` requires.
- iOS/ForgeKit cards are out of scope for this branch. Unknown turns already
  degrade to generic cards.

## Phased delivery (each phase is shippable and testable)

**P0: groundwork and fixes found during research**
- `build_claude_mcp_config` drops `type` and `headers` for URL servers.
  `normalize_mcp_servers` drops `headers` and `bearer_token_env_var`.
- `FORGE_PRESENT_FILE_URL` is only set by the tmux transport
  (`tmux_interactive.py:2819`), so present-file fails in Codex, SDK and subprocess
  sessions.
- present-file staging fails in local mode. `SkuldSettings.home_path` is
  `{persistence_mount_path}/{id}/home` (`src/skuld/config.py:863`), which does not
  exist on a local host. `_presented_staging_dir` resolves it with `strict=True`,
  so the endpoint answers `could not stage file` (reproduced on thor, 2026-09-23).
  Home-directory files are rejected as outside the session roots for the same reason.
- Scrub the session environment, and add the loopback route secret.

**P1: the durable core (backend)**
- The migration, the projection in the log-insert transaction (agent turns,
  `reply_ready`, attention) and the repository port.
- REST: `GET /notifications`, `GET /sessions/{id}/notifications` and
  `POST /notifications/read`, with Cerbos/owner checks.
- The SSE event and the facade handlers.
- Tests: duplicate submits, offline retry, restart replay, cursor paging, and
  isolation between readers and between owners.

**P2: Forge MCP**
- The `skuld.forge_mcp` stdio server and the broker `/api/forge-mcp/*` routes.
- The `forge_notify`, `forge_environment` and read tools.
- Built-in injection for Claude (tmux and SDK) and Codex, and the instructions.
- Proven on a fresh session of each engine.

**P3: web**
- The inline card, the Notifications page with filters and live updates, and the
  unread badge.

**P4: delivery**
- The niuu sink port, the adapter move, the outbox dispatcher and rules. Rules
  start in config, with a settings UI after that.
- Telegram is opted in end to end.

**P5: supervision and reach**
- Scoped workload tokens, and granted peer tools (message, create, start, stop).
- Forge-hosted `/mcp/forge` over HTTP for external agents.
- A `forge-notify` skill in the skills bundle, Grok ACP MCP support, and iOS cards.

## Open questions

1. **MCP hosting.** Broker-hosted stdio (recommended above) or the design doc's
   Forge-hosted HTTP?
2. **Automatic `reply_ready`.** One per final output, shown in the feed with
   external delivery off (recommended), or opt-in per session?
3. **v1 supervision scope.** Read-only peers plus notify (recommended), or include
   `send_message` and lifecycle behind a grant from day one?
4. **Telegram target.** Reuse the owner's existing MESSAGING integration
   connection through Ting's resolver (recommended), or a Forge config section?
