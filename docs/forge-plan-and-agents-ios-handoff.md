# Forge plan & running-agents — iOS handoff

> **Audience:** an iOS engineer building against the Forge session API. You can
> implement everything here **without reading the backend source.** Every JSON
> shape, enum, and reconciliation rule below was verified against the emitting
> code (`src/skuld/transports/tmux_interactive.py`) and the broker
> (`src/skuld/broker.py`). The companion design/decision log is
> `docs/forge-plan-and-agents-surfacing.md`.

---

## 1. Overview & scope

This feature surfaces, for a **single Forge coding session**, two live data
structures:

- **Plan** — the task checklist Claude maintains via its `TodoWrite` tool. A flat
  list of tasks, each with a `status`. This is Claude's own to-do list for the
  current work.
- **Agents** — the sub-processes running *inside* the session right now:
  - **subagents** — Claude `Task`-tool invocations (e.g. a `code-reviewer`
    subagent), and Claude's purpose-built `SubagentStart`/`SubagentStop` hooks.
  - **teammates** — non-primary tmux panes from `--teammate-mode tmux`
    (agent-teams). Each extra pane is treated as a running teammate agent.

**Scope limit — tmux-interactive Claude sessions only.** This data is produced by
`TmuxInteractiveTransport` (Claude Code running in a tmux pane with hooks
enabled). Sessions on other transports (SDK, Codex, OpenCode) do **not** emit
these frames — your code should treat plan/agents as *optional* and degrade to an
empty/hidden state when they never arrive.

**Two delivery channels, must be reconciled.** The same data arrives:

1. **Live** — pushed over the session **WebSocket** as `plan` and `agent_update`
   frames, as state changes.
2. **On-demand** — pulled over **REST** (`GET .../api/plan`, `GET .../api/agents`)
   as a point-in-time snapshot.

You will seed from REST, then stay current via the WebSocket, and re-reconcile on
every reconnect (see §6, §7).

---

## 2. Transport — how iOS connects

### 2.1 WebSocket (live)

These frames flow over **the same Forge session WebSocket your client already
uses** — the broker's browser/session channel. You do **not** open a new socket.
You will already be parsing frames discriminated by a top-level `"type"` field
(e.g. `assistant`, `result`, `ask_user_question`, `claude_permission_request`).
The two new frame types are:

- `type == "plan"`
- `type == "agent_update"`

> **These frames are purely ADDITIVE.** Existing consumers that don't know these
> `type` values simply ignore them (you should already have a default/skip branch
> for unknown frame types). Nothing about the existing transcript stream changes —
> the bare `assistant`/`tool_use` frames are still emitted for transcript history
> alongside these.

### 2.2 REST (pull) — via the niuu session proxy

The endpoints live on the per-session broker FastAPI app, reached through the niuu
session proxy. Base path:

```
/s/{session_id}/api/...
```

So the two endpoints are:

| Method | Path | Returns |
|---|---|---|
| `GET` | `/s/{session_id}/api/plan` | current plan snapshot |
| `GET` | `/s/{session_id}/api/agents` | currently-running agents snapshot |

`{session_id}` is the Forge session id you already hold for the open session.
Authentication / host are the same as every other `/s/{session_id}/api/*` call you
make today.

---

## 3. Live WebSocket frames

### 3.1 `plan` frame

Emitted **on every `TodoWrite`** Claude performs. The entire task list is sent each
time.

```json
{
  "type": "plan",
  "event_type": "claude.plan",
  "tasks": [
    {"content": "Wire the endpoint", "status": "completed", "activeForm": "Wiring the endpoint"},
    {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"},
    {"content": "Update docs", "status": "pending"}
  ],
  "counts": {"total": 3, "pending": 1, "in_progress": 1, "completed": 1},
  "metadata": {"source": "claude_hook"}
}
```

**Top-level fields**

| Field | Type | Notes |
|---|---|---|
| `type` | string | Always `"plan"`. Use as the discriminator. |
| `event_type` | string | Always `"claude.plan"`. Informational. |
| `tasks` | array | The full task list (see below). May be empty `[]`. |
| `counts` | object | Aggregate counts (see below). |
| `metadata.source` | string | `"claude_hook"` for live frames; `"reconnect_replay"` is **not** used for plan replay (the original frame is replayed verbatim, so its source stays `"claude_hook"`). |

**Task object** (each element of `tasks`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `content` | string | yes | The task text. Always present (the backend drops tasks with empty content). |
| `status` | string | yes | One of `pending` / `in_progress` / `completed` — **but see §5: an unrecognized value can pass through.** |
| `activeForm` | string | **optional** | Present-tense phrasing (e.g. "Adding tests"). **Omitted entirely when empty** — do not assume the key exists. |

**`counts` object**

| Field | Type | Notes |
|---|---|---|
| `total` | int | Number of tasks. Always present. |
| `pending` | int | Tasks with `status == "pending"`. |
| `in_progress` | int | Tasks with `status == "in_progress"`. |
| `completed` | int | Tasks with `status == "completed"`. |

> In the **WebSocket** frame, `counts` always carries all four keys
> (`total`, `pending`, `in_progress`, `completed`). Note the REST endpoint differs
> on the empty case — see §4.

**Semantics — full replace, last-writer-wins.** Each `plan` frame is the
**complete** current list. It is **never a delta.** When you receive a `plan`
frame, **replace your entire plan state** with `tasks`/`counts` from that frame.
Do not merge by index or by content. (Task ids are not stable, so there is nothing
reliable to merge on; the backend deliberately does whole-list replacement.)

### 3.2 `agent_update` frame

Emitted when an agent starts or stops.

**`started` (subagent):**

```json
{
  "type": "agent_update",
  "event_type": "claude.agent",
  "action": "started",
  "agent": {
    "id": "toolu_01ABC...",
    "kind": "subagent",
    "name": "code-reviewer",
    "status": "running",
    "description": "Review the diff",
    "started_at": "2026-06-25T18:42:01.123456+00:00"
  },
  "metadata": {"source": "claude_hook"}
}
```

**`started` (teammate):**

```json
{
  "type": "agent_update",
  "event_type": "claude.agent",
  "action": "started",
  "agent": {
    "id": "%3",
    "kind": "teammate",
    "name": "reviewer-pane",
    "status": "running",
    "current_command": "claude"
  },
  "metadata": {"source": "claude_hook"}
}
```

**`stopped` (terminal):**

```json
{
  "type": "agent_update",
  "event_type": "claude.agent",
  "action": "stopped",
  "agent": {
    "id": "toolu_01ABC...",
    "kind": "subagent",
    "name": "code-reviewer",
    "status": "done",
    "description": "Review the diff",
    "started_at": "2026-06-25T18:42:01.123456+00:00"
  },
  "metadata": {"source": "claude_hook"}
}
```

**Top-level fields**

| Field | Type | Notes |
|---|---|---|
| `type` | string | Always `"agent_update"`. Discriminator. |
| `event_type` | string | Always `"claude.agent"`. Informational. |
| `action` | string | `"started"` or `"stopped"`. |
| `agent` | object | The agent (union shape below). |
| `metadata.source` | string | `"claude_hook"` live; `"reconnect_replay"` on reconnect (see §6). |

**`agent` object — union over `kind`**

Common fields (always present on both kinds):

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable id for this agent. **Key your dictionary on this.** For subagents it's the Task `tool_use_id`; for teammates it's the tmux `pane_id` (e.g. `"%3"`). |
| `kind` | string | `"subagent"` or `"teammate"`. |
| `name` | string | subagent: `subagent_type` (or `description`, falling back to `"subagent"`). teammate: the pane's `window_name` (or `"pane <index>"`). |
| `status` | string | `"running"`, `"done"`, or `"failed"`. `running` on `started`; `done`/`failed` on `stopped`. |

Subagent-only fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `description` | string | optional | Task description / prompt summary. Omitted when empty. |
| `model` | string | optional | Only set by the `SubagentStart` hook when it carries a model. Omitted otherwise. |
| `started_at` | string (ISO-8601, UTC) | optional | e.g. `"2026-06-25T18:42:01.123456+00:00"`. Set once when the agent is first registered. |

Teammate-only fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `current_command` | string | optional-ish | The pane's foreground command (e.g. `"claude"`). Always emitted for teammates but may be `""`. |

> **Cross-kind absence is normal:** teammates do **not** carry
> `description`/`model`/`started_at`; subagents do **not** carry
> `current_command`. Treat all of these as optional in your model.

**Lifecycle semantics**

- **`started` may be re-emitted for the same `id`.** This is a **merge/enrich, not
  a duplicate.** Example: the `Task` tool fires first and registers the subagent;
  a subsequent `SubagentStart` hook carrying the same `tool_use_id` re-emits
  `started` with `model`/`description` filled in. **Upsert by `id`** — overwrite /
  merge the existing entry, never append a second row.
- **`stopped` fires once** for an agent, with a terminal `status` (`done` on
  success, `failed` on error/interrupt). On `stopped`, **remove** the agent from
  your running map.

---

## 4. Pull endpoints (REST snapshots)

These reflect **live broker in-memory state at the moment of the call.** They are
plain snapshots — **the response shape is NOT the same as the WS frame**
(no `type` / `event_type` / `action` / `metadata` wrapper).

### 4.1 `GET /s/{session_id}/api/plan`

Returns **only** `tasks` + `counts`.

**Populated:**

```json
{
  "tasks": [
    {"content": "Wire the endpoint", "status": "completed", "activeForm": "Wiring the endpoint"},
    {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"}
  ],
  "counts": {"total": 2, "pending": 0, "in_progress": 1, "completed": 1}
}
```

**Empty (no `TodoWrite` has happened yet):**

```json
{"tasks": [], "counts": {"total": 0}}
```

> ⚠️ **Empty-state quirk:** when there is no plan yet, `counts` is
> **`{"total": 0}`** — the `pending`/`in_progress`/`completed` keys are
> **absent.** When a plan exists, all four keys are present (echoed from the WS
> frame). **Always default missing count keys to `0`** rather than force-unwrapping.

### 4.2 `GET /s/{session_id}/api/agents`

Returns **bare agent objects** — the same `agent` object shape as inside the WS
frame, but with **no `action` field** and no wrapper. The list contains **only
currently-running agents**; stopped agents are gone.

**Populated:**

```json
{
  "agents": [
    {
      "id": "toolu_01ABC...",
      "kind": "subagent",
      "name": "code-reviewer",
      "status": "running",
      "description": "Review the diff",
      "started_at": "2026-06-25T18:42:01.123456+00:00"
    },
    {
      "id": "%3",
      "kind": "teammate",
      "name": "reviewer-pane",
      "status": "running",
      "current_command": "claude"
    }
  ]
}
```

**Empty (no agents running):**

```json
{"agents": []}
```

> Every agent returned here has `status: "running"` — by construction the endpoint
> only holds live agents. You will never see `done`/`failed` from this endpoint
> (those come only via a `stopped` WS frame, then the agent is dropped).

---

## 5. Status enums

Treat these as **closed sets for your UI styling**, but **forward-compatible** in
parsing — the backend does **not** strictly clamp every value.

### Task status (`tasks[].status`)

Canonical set:

| Value | Meaning |
|---|---|
| `pending` | Not started. |
| `in_progress` | Currently being worked. |
| `completed` | Done. |

The backend normalizes common synonyms (`done`/`complete`/`finished` →
`completed`; `running`/`active`/`doing` → `in_progress`). **However, an
unrecognized value passes through unchanged** (e.g. a future status Claude
invents). **Decode unknown task status into a safe fallback** (recommended:
`.unknown`, rendered like `pending`) — never crash or drop the task.

### Agent kind (`agent.kind`)

| Value | Meaning |
|---|---|
| `subagent` | Claude `Task` / `SubagentStart`. |
| `teammate` | A tmux teammate pane. |

These are the only values the code emits, but **decode defensively** — treat an
unrecognized `kind` as a generic agent (render under an "Other" group) rather than
failing.

### Agent status (`agent.status`)

| Value | When |
|---|---|
| `running` | On `started` frames and in the `/api/agents` snapshot. |
| `done` | On `stopped`, success. |
| `failed` | On `stopped`, error / interrupt. |

Same rule: **decode unknown agent status into a safe fallback** (treat as
terminal-unknown).

---

## 6. Reconnect / replay semantics

When you (re)connect the session WebSocket, the broker first replays history
(transcript, pending questions/permissions). **After that**, it replays the
current plan and the running fleet:

1. **Plan** — if a plan exists, the broker replays **the current `plan` frame
   exactly once**, verbatim (so `metadata.source` is still `"claude_hook"`).
2. **Agents** — for **each currently-running agent**, the broker emits **one
   `agent_update` with `action: "started"`** and **`metadata.source:
   "reconnect_replay"`**.

> **Stopped agents are NOT replayed.** Any agent that finished while you were
> disconnected is simply absent from the replay — there is no `stopped` frame to
> catch up on.

**How iOS must treat the replay:**

- The replayed `started` set is **authoritative for "currently running."** It is
  the complete current truth, not an increment.
- Therefore, on reconnect you should **reset (rebuild) your running-agents map
  from the replayed `started` frames**, *not* merge into your stale map. This is
  how you drop agents that ended while you were offline (no `stopped` arrives for
  them).
- The replayed `plan` frame replaces your plan as usual (full replace).
- You can detect replay frames by `metadata.source == "reconnect_replay"` if you
  want to special-case the "rebuild" behavior; otherwise the simplest correct
  approach is to **clear your running-agents map right before processing the
  post-history replay batch and let the replayed `started` frames repopulate it.**

---

## 7. iOS rendering & reconciliation workflow

A concrete recipe. Numbered for the open-session path:

1. **On session open — seed from REST first** (so the UI isn't blank before the
   first WS frame lands):
   - `GET /s/{id}/api/plan` → set initial `plan`.
   - `GET /s/{id}/api/agents` → set initial `agents` map (key by `agent.id`).
2. **Subscribe to the session WebSocket** (the one you already use). Branch on
   the frame `type`:
   - `"plan"` → **replace** your entire `plan` value with the frame's
     `tasks`/`counts`. (Never merge.)
   - `"agent_update"` →
     - `action == "started"` → **upsert** `agent` into the map by `agent.id`.
     - `action == "stopped"` → **remove** `agent.id` from the map.
   - anything else → ignore (unchanged existing behavior).
3. **On reconnect** (socket dropped and re-established):
   - Optionally re-pull REST as a belt-and-suspenders seed.
   - When the post-history replay batch arrives: **replace** plan from the
     replayed `plan` frame, and **rebuild** the agents map from the replayed
     `started` set (treat it as the full current truth — clear then repopulate, so
     agents that ended while offline are dropped).

**State you maintain**

- `plan`: a single value, **replaced wholesale** on each `plan` frame.
- `agents`: a **dictionary keyed by `agent.id`** — upsert on `started`, remove on
  `stopped`, rebuild on reconnect.

**Rendering**

- **Plan** → a checklist:
  - per-task row with `content` and per-`status` styling (e.g. checkbox filled for
    `completed`, spinner/highlight for `in_progress`, muted for `pending`,
    neutral for `.unknown`).
  - optionally show `activeForm` (present-tense) for the `in_progress` task.
  - a progress summary derived from `counts` (e.g. `completed / total`, or a
    progress bar). Default missing count keys to `0`.
- **Agents** → a "running" list **grouped by `kind`** (Subagents / Teammates /
  Other):
  - show `name`, plus `description` for subagents or `current_command` for
    teammates.
  - show a spinner while `status == "running"`. (In the running list every agent
    is `running`; `done`/`failed` arrive only as the removal signal.)

### 7.1 Suggested Swift model sketch

Idiomatic Codable with `.unknown` fallbacks for forward-compat. Keep enums lenient.

```swift
// MARK: - Enums (forward-compatible)

enum TaskStatus: String, Codable {
    case pending, inProgress = "in_progress", completed, unknown
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TaskStatus(rawValue: raw) ?? .unknown
    }
}

enum AgentKind: String, Codable {
    case subagent, teammate, unknown
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = AgentKind(rawValue: raw) ?? .unknown
    }
}

enum AgentStatus: String, Codable {
    case running, done, failed, unknown
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = AgentStatus(rawValue: raw) ?? .unknown
    }
}

// MARK: - Plan

struct PlanTask: Codable, Identifiable {
    var id: String { content }          // ids aren't stable; content is the render key
    let content: String
    let status: TaskStatus
    let activeForm: String?             // optional — omitted when empty
}

struct PlanCounts: Codable {
    let total: Int
    let pending: Int?                   // absent in empty REST response → default 0
    let inProgress: Int?
    let completed: Int?
    enum CodingKeys: String, CodingKey {
        case total, pending, completed
        case inProgress = "in_progress"
    }
}

/// Use this for BOTH the WS `plan` frame (extra wrapper keys ignored) and the
/// REST `/api/plan` body.
struct Plan: Codable {
    let tasks: [PlanTask]
    let counts: PlanCounts
}

// MARK: - Agents

struct Agent: Codable, Identifiable {
    let id: String
    let kind: AgentKind
    let name: String
    let status: AgentStatus
    let description: String?            // subagent-only
    let model: String?                  // subagent-only
    let startedAt: String?             // subagent-only, ISO-8601 UTC
    let currentCommand: String?         // teammate-only
    enum CodingKeys: String, CodingKey {
        case id, kind, name, status, description, model
        case startedAt = "started_at"
        case currentCommand = "current_command"
    }
}

/// WS `agent_update` frame.
struct AgentUpdate: Codable {
    enum Action: String, Codable { case started, stopped }
    let action: Action
    let agent: Agent
}

/// REST `/api/agents` body.
struct AgentsSnapshot: Codable {
    let agents: [Agent]
}
```

> The WS `plan` frame carries extra keys (`type`, `event_type`, `metadata`) that
> `Plan` ignores; decode the frame envelope by `type` first, then decode the same
> object into `Plan`. Likewise decode the `agent_update` envelope by `type`, then
> into `AgentUpdate`.

---

## 8. Caveats & open questions

Carried over from the backend decision log — the parts that affect iOS:

- **`SubagentStart`/`Stop` field names are best-effort.** They're parsed
  defensively and deduped on `tool_use_id` when present. In rare cases where dedup
  misses, **the same logical agent could briefly appear twice.** Mitigation on
  your side: **key strictly on `agent.id`** (upsert, never append) and **tolerate a
  stray duplicate gracefully** — your dictionary-by-id approach already does this.
- **Teammate detection is heuristic.** Teammates are inferred from non-primary
  tmux panes (pane index `0` is the main Claude REPL and is excluded). A pane that
  isn't really an agent could in principle show up as a teammate; render
  conservatively.
- **REST reflects live broker memory only — no durable history.** If the broker
  restarts, `/api/plan` and `/api/agents` reset to empty until Claude emits again.
  Don't treat these as persistent records; they're a live mirror. (A durable
  event-log reduction is noted as future work but not available yet.)
- **`started_at` is the only timestamp**, and it's subagent-only. Teammates have
  no start time. Don't rely on timestamps for ordering teammates.
- **No `TaskCreated`/`TaskCompleted` surfacing yet.** Agent-team task assignments
  are deferred (their payloads are unobserved). Don't build UI expecting them.
- **`counts` empty-state asymmetry** (restated because it bites): the REST empty
  plan returns `counts: {"total": 0}` with the per-status keys absent, while
  populated plans and all WS frames include all four keys. Default missing keys
  to `0`.
