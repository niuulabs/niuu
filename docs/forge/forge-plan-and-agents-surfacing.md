# Forge tmux — surfacing the live plan & running agents

> Scope: **Claude Code tmux-interactive sessions only**
> (`skuld.transports.tmux_interactive.TmuxInteractiveTransport`). Status: initial
> implementation. This doc is both the contract and the **decision log** — the
> `Decisions` section records choices made where the design was open, with the
> alternatives, so we can review and refactor later.

## Goal

Let the Forge UI show, at any moment, **(a) Claude's current plan** — the task
checklist Claude maintains with its task tools (`TaskCreate`/`TaskUpdate`, or
`TodoWrite` on older Claude Code versions), with each task's status —
and **(b) the agents/sub-processes running inside the session** — Claude `Task`
subagents and `--teammate-mode tmux` team panes. Both as structured data (not
scraped terminal text), live over the session WebSocket and pullable over REST.

## How Claude exposes this in tmux (the raw signals)

A tmux session runs `claude` with hooks enabled (`--settings` → POST to the
broker's `/api/claude/hooks`). The relevant hooks are **already registered** in
`_CLAUDE_HOOK_EVENTS`; we just special-case them:

| Signal | Hook | Carries |
|---|---|---|
| Plan / tasks | `PostToolUse` with `tool_name` `TaskCreate` / `TaskUpdate` / `TaskGet` / `TaskList` | One task per call. `tool_response` is the tool's output: TaskCreate `{task: {id, subject}}` (status starts `pending`, `activeForm` comes from `tool_input`), TaskUpdate `{success, taskId, updatedFields, statusChange?: {from, to}}`, TaskGet `{task \| null}`, TaskList `{tasks: [{id, subject, status, owner?, blockedBy}]}` |
| Plan / tasks (older Claude Code) | `PreToolUse` with `tool_name="TodoWrite"` | `tool_input.todos: [{content, status, activeForm?}]` — the **full** list each call (TodoWrite replaces the whole list) |
| Subagent start | `PreToolUse` with `tool_name="Task"` | `tool_input: {description, prompt, subagent_type}`, `tool_use_id` |
| Subagent end | `PostToolUse` with the Task's `tool_use_id` | `tool_response` |
| Team member | `terminal_pane_opened` / `terminal_pane_closed` | `pane_id`, `window_name`, `current_command`, `active` (already emitted by pane discovery) |

TodoWrite `status` values normalize to `pending | in_progress | completed` (others
pass through). Task-tool statuses must be one of those three; anything else, or a
`tool_response` without the shape above, is reported as a `system` frame with
`subtype: "claude_plan_unavailable"` (plus an error log) and leaves the plan as it
was — never a silently stale plan. The Task `tool_use_id` is the subagent's stable id; team agents key on
`pane_id`.

## Event contracts (broker → channels)

### `plan` — Claude's current task list
```json
{
  "type": "plan", "event_type": "claude.plan",
  "tasks": [
    {"content": "Wire the endpoint", "status": "completed", "activeForm": "Wiring the endpoint"},
    {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"}
  ],
  "counts": {"total": 2, "pending": 0, "in_progress": 1, "completed": 1},
  "metadata": {"source": "claude_hook"}
}
```
Emitted on every `TodoWrite`, and on every task-tool call that changes the task
list. The broker keeps the latest as `_current_plan`.

### `agent_update` — one running agent's lifecycle
```json
{
  "type": "agent_update", "event_type": "claude.agent",
  "action": "started",                // started | stopped
  "agent": {
    "id": "<tool_use_id|pane_id>",
    "kind": "subagent",               // subagent (Task) | teammate (pane)
    "name": "code-reviewer",          // subagent_type/description, or window_name
    "status": "running",              // running | done | failed
    "description": "Review the diff",
    "started_at": "2026-06-25T..."
  }
}
```
The broker keeps the live set as `_running_agents` (dict by `id`).

## Broker state, broadcast, replay

Mirrors the `_pending_ask_user_questions` pattern exactly:
- `_current_plan: dict | None` and `_running_agents: dict[str, dict]`.
- `_handle_cli_event` folds `plan` / `agent_update` / pane open-close into that
  state, enqueues to the durable event log, and broadcasts to channels.
- On WebSocket reconnect, after history, the broker replays the current `plan`
  and one `agent_update` per running agent — so a late-joining client immediately
  knows the plan and the fleet (same guarantee questions/permissions already get).

## Read API (pull)

- Broker app (the per-session FastAPI server): `GET /api/plan` → `_current_plan`,
  `GET /api/agents` → `{agents: [...]}`. Self-contained, reflects live in-memory
  state, reachable through the niuu session proxy at `/s/{session_id}/api/*`.
- Forge API (Volundr), when present, exposes `GET /api/v1/forge/sessions/{id}/plan`
  and `/agents` — see Decisions for the proxy-vs-durable-log choice.

## Testing (the tmux simulation framework)

`fakeagent` gains two pure-stdlib directives that POST the real hook shapes:
- `todo:<c1>=<status>;<c2>=<status>;...` → a `TodoWrite` PreToolUse with that list.
- `agent:<name>|<description>` → a `Task` PreToolUse (start); `agent_done:<name>`
  → the matching `PostToolUse`.

Tests cover: transport emits normalized `plan` / `agent_update`; broker tracks
`_current_plan` / `_running_agents`; reconnect replays both; the `/api/plan` and
`/api/agents` endpoints return live state; team panes appear as `teammate` agents.

## Decisions (and the alternatives, for later review)

1. **Hooks, not TTY scraping, for the plan.** Structured, exact, reconnect-safe.
   *Alt:* `capture-pane` + parse the checklist — kept only as a future fallback
   for hooks-off sessions; brittle to CLI UI changes.
2. **Plan is last-writer-wins (full replace).** `TodoWrite` sends the whole list
   each call, so `_current_plan` is simply the latest. *Alt:* maintain a merged
   model keyed by task id — unnecessary given the full-list semantics, and
   `TodoWrite` task ids aren't reliably stable.
   **Task tools (2026-11):** Claude Code dropped `TodoWrite` for `TaskCreate` /
   `TaskUpdate`, which change one task per call, so the transport keeps the list
   (keyed by Claude's task id, in creation order) and still emits the full list.
   It reads `PostToolUse`, because only TaskCreate's response carries the new id
   and only TaskUpdate's response says whether Claude applied the change.
   `TaskGet`/`TaskList` responses are authoritative and resync the list (TaskList
   omits `activeForm`, so known ones are kept). A TaskCreate returning an id the
   list already holds means a new list (ids never repeat within one), so the plan
   restarts. Hooks from another Claude process (an agent-teams teammate, different
   `session_id`) are ignored: it works its own list. An update for a task the
   transport never saw created (e.g. a broker restart mid-session) is reported as
   `claude_plan_unavailable` until the agent's next `TaskList`.
3. **"Agents" = Task subagents (hooks) ∪ teammate panes (pane events).** These are
   the two real sources a Claude tmux session exposes. **`SubagentStart`/`Stop` are
   now also wired** — Claude's purpose-built subagent-lifecycle hooks. All agent
   signals flow through one `_start_agent`/`_finish_agent` core keyed by a resolved
   id (`_resolve_agent_id` prefers `tool_use_id`), so a `SubagentStart` carrying the
   Task's `tool_use_id` **merges** with the Task entry instead of double-counting.
   *Still deferred:* `TaskCreated`/`TaskCompleted` (agent-team task assignments) —
   their payloads are unobserved and the meaning is ambiguous; the harness can add
   them once we capture a real one. *Validated live against real Claude
   (2026-06-25):* `SubagentStart` = `{agent_id, agent_type, cwd, session_id,
   transcript_path}` — the id is `agent_id`, the name is `agent_type` (e.g.
   `general-purpose`); `SubagentStop` carries `agent_id`. The spawn tool is named
   `Agent` (not `Task`) with input `{description, prompt, subagent_type}`, and its
   `tool_use_id` does NOT equal the `agent_id` — so subagents are surfaced from
   `SubagentStart/Stop` only (single source); the `Agent`/`Task` PreToolUse is NOT
   also surfaced (it would double-count). Parsing stays defensive (other spellings
   accepted). *Open follow-up:* attach the `Agent` tool's richer `description` to the
   subagent (blocked by the id mismatch).
4. **Distinct top-level event types `plan` / `agent_update`** (not buried inside
   `assistant`/`tool_use`). The bare `tool_use` frame is still emitted for
   transcript history; these are additive, so existing consumers are unaffected.
5. **Read endpoints live on the broker** (live in-memory truth) and are reached
   via the existing niuu session proxy. *Alt / follow-up:* a Volundr forge route
   that proxies to the broker, or reduces the durable event log (like
   `transcript_rebuild`) so the plan/agents survive a broker restart. Proxy chosen
   as the natural first step; durable-log reduction noted for later if we need
   post-mortem/offline access.
6. **`niuu`-side, no new shared models.** All new state is broker/transport-local
   plain dicts; no `niuu` port changes, keeping the blast radius small.
