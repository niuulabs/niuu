# OpenClaw Session Orchestrator Guide

This guide teaches an AI controller how to behave like a Tyr-style orchestrator against Volundr/Forge session runtimes.

The target use case is:

- OpenClaw owns campaign or task logic.
- Volundr/Forge owns session lifecycle.
- Skuld owns the live in-session chat stream.
- The controller must consume SSE, manage sessions, send messages, observe streamed output, read chronicles, and decide when work is complete.

This is written for machine operators and agent builders, not end users.

## 1. Mental model

Treat the platform as three separate planes:

### Control plane

Use the Forge/Volundr HTTP API to:

- create sessions
- start and stop sessions
- list and inspect sessions
- fetch conversation history
- fetch chronicles and timelines
- query event history
- inspect PR and diff state

### State plane

Use the session SSE stream to observe:

- session lifecycle changes
- activity changes
- chronicle-related events
- aggregate stats updates

SSE is the best way to drive a controller loop in near real time.

### Data plane

Use the live session WebSocket to:

- send prompts into a running session
- receive assistant streaming output
- observe tool/thought activity
- detect turn completion

The most important architectural rule is:

> SSE tells you what the session is doing at a platform level.
> The live WebSocket tells you what the agent is saying and doing inside the session.

Do not confuse them.

## 2. Route prefixes and compatibility

The core session router is mounted under both:

- `/api/v1/forge`
- `/api/v1/volundr`

For a new controller, prefer `forge` as the canonical session API namespace.

Important caveat:

- some auxiliary routes, especially local-workspace git routes such as PR status and local diff helpers, are implemented under `/api/v1/volundr/...`
- if you need maximum deployment compatibility, support both prefixes when discovering endpoints

Recommended default:

- use `/api/v1/forge/...` for session lifecycle, conversation, chronicles, transcript, and SSE
- fall back to `/api/v1/volundr/...` for compatibility
- treat `/api/v1/volundr/sessions/{id}/pr` as the safe PR-status route unless your deployment explicitly exposes the forge alias too

## 3. Authentication model

Use Bearer auth for all HTTP requests:

```http
Authorization: Bearer <token>
```

For WebSockets:

- preferred: Bearer token in headers
- supported fallback: append `?access_token=<token>` to the WebSocket URL

If your WebSocket client cannot set headers, use the query parameter fallback.

## 4. Session object fields that matter

When you fetch or receive session state, these fields are the most important:

- `id`
- `name`
- `status`
- `chat_endpoint`
- `code_endpoint`
- `message_count`
- `tokens_used`
- `error`
- `owner_id`
- `activity_state`
- `activity_metadata`
- `workload_type`

You should cache the latest session object keyed by `session_id`.

## 5. Session lifecycle API

Preferred control-plane endpoints:

| Method | Path | Use |
|---|---|---|
| `GET` | `/api/v1/forge/sessions` | List sessions |
| `GET` | `/api/v1/forge/sessions/{id}` | Fetch one session |
| `POST` | `/api/v1/forge/sessions` | Create and start a session |
| `PUT` | `/api/v1/forge/sessions/{id}` | Update mutable fields |
| `POST` | `/api/v1/forge/sessions/{id}/start` | Restart a stopped or failed session |
| `POST` | `/api/v1/forge/sessions/{id}/stop` | Stop a running session |
| `PATCH` | `/api/v1/forge/sessions/{id}/archive` | Archive a session |
| `PATCH` | `/api/v1/forge/sessions/{id}/restore` | Restore an archived session |
| `DELETE` | `/api/v1/forge/sessions/{id}` | Delete a session |

The session status model is:

```text
CREATED -> STARTING -> PROVISIONING -> RUNNING -> STOPPING -> STOPPED -> ARCHIVED
                                          \-> FAILED
```

Recommended interpretation:

- `CREATED`, `STARTING`, `PROVISIONING`: not yet interactive
- `RUNNING`: live chat transport should exist
- `STOPPING`: do not enqueue new work
- `STOPPED`: terminal state for runtime, but artifacts are still retrievable
- `FAILED`: terminal failure unless you retry
- `ARCHIVED`: historical only

## 6. Create a session

Minimal example:

```json
{
  "name": "openclaw-task-123",
  "model": "claude-sonnet-4-6",
  "source": {
    "type": "git",
    "repo": "https://github.com/acme/repo",
    "branch": "main",
    "base_branch": "main"
  },
  "system_prompt": "You are the implementation worker.",
  "initial_prompt": "Fix issue ACME-123 and open a PR.",
  "issue_id": "ACME-123",
  "issue_url": "https://linear.app/acme/issue/ACME-123",
  "workload_type": "session",
  "profile_name": "default",
  "integration_ids": []
}
```

The response should be treated as an asynchronous launch acknowledgement, not proof that the session is ready.

After creation:

1. cache the `session_id`
2. subscribe to SSE if not already subscribed
3. wait for `status=running`
4. verify `chat_endpoint` exists before attempting live interaction

## 7. Real-time session SSE

Subscribe here:

- `GET /api/v1/forge/sessions/stream`
- legacy alias: `GET /api/v1/volundr/sessions/stream`

This stream is for platform-level events, not chat token streaming.

### Event types you should handle

Documented session-stream events:

- `session_created`
- `session_updated`
- `session_deleted`
- `stats_updated`
- `heartbeat`
- `chronicle_created`
- `chronicle_updated`
- `chronicle_deleted`
- `pr_created`
- `pr_merged`

Important real-world event:

- `session_activity`

`session_activity` is emitted when the live broker reports activity state changes back to Volundr. Tyr relies on it, and your controller should too.

### `session_updated` payload

Treat this as a partial session snapshot. It commonly includes:

- `id`
- `name`
- `model`
- `repo`
- `branch`
- `status`
- `chat_endpoint`
- `code_endpoint`
- `created_at`
- `updated_at`
- `last_active`
- `message_count`
- `tokens_used`
- `pod_name`
- `error`
- `tracker_issue_id`
- `workload_type`
- `owner_id`
- `tenant_id`

### `session_activity` payload

Expected shape:

```json
{
  "session_id": "uuid",
  "state": "active",
  "metadata": {
    "turn_count": 3,
    "duration_seconds": 41
  },
  "owner_id": "user-id"
}
```

Known activity states:

- `active`
- `idle`
- `tool_executing`

### SSE consumer rules

Build your SSE client with these behaviors:

1. Reconnect automatically.
2. Treat heartbeats as proof of liveness only.
3. Maintain a per-session cache of the latest `session_updated` payload.
4. Maintain the latest `session_activity` payload separately.
5. On reconnect, rehydrate truth from `GET /sessions/{id}` for all active sessions.
6. Never assume SSE is durable. It is an in-memory broadcast channel, not a persisted queue.

Recommended timeout strategy:

- if the stream goes silent beyond your liveness threshold, reconnect
- after reconnect, refresh all sessions you still care about

## 8. Live chat transport

The live agent stream does not come from the Volundr API server. It comes from the session broker reachable at `chat_endpoint`.

You will usually get `chat_endpoint` from:

- `GET /api/v1/forge/sessions/{id}`
- `session_updated` SSE events

Typical shape:

- `ws://host/s/<session_id>/session`
- `wss://host/s/<session_id>/session`

### When to use direct WebSocket

Use the direct WebSocket when you need:

- token or chunk streaming
- tool/thought visibility
- interactive control
- low latency

### When to use the HTTP proxy instead

Use the HTTP proxy endpoints when you only need:

- fire-and-forget message submission
- conversation history after the fact
- compatibility with simpler HTTP-only workers

## 9. WebSocket protocol for the live session

Connect to:

```text
<chat_endpoint>
```

If needed:

```text
<chat_endpoint>?access_token=<token>
```

### What to send

The simplest valid user frame is:

```json
{
  "type": "user",
  "content": "Implement the requested change."
}
```

A backward-compatible shorthand also works:

```json
{
  "content": "Implement the requested change."
}
```

### First frames you may receive

On connect, expect some or all of:

- `system`
- `capabilities`
- `conversation_history`
- room-state events for workflow or multi-agent sessions

Example:

```json
{"type":"system","content":"Connected to session <id>"}
```

### Output frames you should understand

The live broker can emit multiple frame styles. Your client should handle at least these:

#### `user_confirmed`

The broker accepted your message and echoed it into session history.

```json
{
  "type": "user_confirmed",
  "id": "message-id",
  "content": "Implement the requested change."
}
```

#### `assistant`

A structured assistant event with content blocks.

Typical shape:

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "I inspected the repository."}
    ]
  }
}
```

Your client should concatenate all `text` blocks into the human-visible assistant turn.

#### `content_block_delta`

Streaming text or reasoning fragments.

Typical text delta:

```json
{
  "type": "content_block_delta",
  "delta": {
    "type": "text_delta",
    "text": "partial output"
  }
}
```

You should append `delta.text` to the in-flight assistant turn buffer.

#### `result`

Marks the end of a turn and often carries usage information.

Typical responsibilities on `result`:

- close the in-flight assistant turn
- persist the completed turn
- mark session activity as idle in your local model
- record usage if you care about cost or token telemetry

#### `error`

Treat as transport or turn failure unless clearly recoverable.

#### `room_message`

Visible participant messages in workflow or room sessions.

#### `room_activity`

High-level participant activity such as:

- `thinking`
- `tool_executing`
- `idle`

#### `room_agent_event`

Internal tool/thought frames for richer UIs or agent observability.

#### `room_notification`

Important workflow notifications, such as an agent needing help.

### Tool visibility

By default, some channels suppress internal tool-use and tool-result blocks.

If you want richer internal event visibility on the WebSocket, you can send:

```json
{
  "type": "set_internal_visibility",
  "visible": true
}
```

This is optional for controllers, but useful for debugging or agent analytics.

## 10. Simpler HTTP-only interaction mode

If your controller does not want to maintain a direct WebSocket, you can still drive a session through HTTP.

### Send a message to a running session

```http
POST /api/v1/forge/sessions/{session_id}/messages
Content-Type: application/json

{"content":"Implement the requested change."}
```

This is a proxy. Under the hood, the platform connects to the live session WebSocket for you and sends the user frame.

Expected response:

```json
{
  "status": "sent",
  "session_id": "<id>"
}
```

### Read conversation history

```http
GET /api/v1/forge/sessions/{session_id}/conversation
```

This works for:

- a live session by proxying to the broker
- a stopped session by falling back to archived transcript data when available

Typical response shape:

```json
{
  "turns": [
    {
      "id": "1",
      "role": "user",
      "content": "Do the work"
    },
    {
      "id": "2",
      "role": "assistant",
      "content": "I changed three files."
    }
  ],
  "is_active": false,
  "last_activity": ""
}
```

### Read persisted transcript directly

```http
GET /api/v1/forge/sessions/{session_id}/transcript
GET /api/v1/forge/sessions/{session_id}/transcript/download?format=md
GET /api/v1/forge/sessions/{session_id}/transcript/download?format=json
```

Use this when:

- the session has already stopped
- you want the durable conversation record
- you do not care about live broker state

### Read archive metadata

```http
GET /api/v1/forge/sessions/{session_id}/archive
```

Useful for post-run harvesters.

## 11. Broker-side HTTP endpoints derived from `chat_endpoint`

If you have a live `chat_endpoint`, you can derive the live broker base URL:

- replace `ws://` with `http://`
- replace `wss://` with `https://`
- strip the trailing `/session`

Example:

```text
wss://host/s/123/session -> https://host/s/123
```

Useful live-broker endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `<base>/api/conversation/history` | Live conversation history |
| `GET` | `<base>/api/capabilities` | Live transport capabilities |
| `GET` | `<base>/api/logs` | Recent broker logs |
| `GET` | `<base>/api/logs/aggregate` | Interleaved participant logs |
| `GET` | `<base>/api/diff/files` | Live changed-file summary |

Use the broker endpoints only when the session is running.

## 12. Event-history API

Use the event repository when you want durable evidence instead of transient SSE.

### Query per-session events

```http
GET /api/v1/volundr/sessions/{session_id}/events
```

Supported filters:

- `event_type`
- `after`
- `before`
- `limit`
- `offset`

Example:

```text
/api/v1/volundr/sessions/<id>/events?event_type=tool_use&limit=200
```

### Get event counts

```http
GET /api/v1/volundr/sessions/{session_id}/events/counts
```

### Get token timeline

```http
GET /api/v1/volundr/sessions/{session_id}/events/tokens?bucket_seconds=300
```

### Useful event types

Common session event types include:

- `message_user`
- `message_assistant`
- `file_created`
- `file_modified`
- `file_deleted`
- `git_commit`
- `git_push`
- `git_branch`
- `git_checkout`
- `terminal_command`
- `tool_use`
- `error`
- `token_usage`
- `session_start`
- `session_stop`

Use these events to answer questions like:

- Did the agent actually write files?
- Did it run tests?
- Did it commit or push?
- Did it call a git or shell tool?
- How much token budget did it burn?

## 13. PR, diff, and git inspection

### PR status

The safe route to use is:

```http
GET /api/v1/volundr/sessions/{session_id}/pr
```

Expected response shape:

```json
{
  "number": 42,
  "url": "https://github.com/acme/repo/pull/42",
  "state": "OPEN",
  "mergeable": "MERGEABLE",
  "checks": [
    {"name":"ci","status":"SUCCESS"}
  ]
}
```

Notes:

- returns `null` if no PR exists
- depends on local workspace git tooling in mini/local mode
- do not assume it exists in every deployment mode unless your operator guarantees it

### Diff helpers

Useful endpoints:

- `GET /api/v1/volundr/sessions/{session_id}/diff`
- `GET /api/v1/volundr/sessions/{session_id}/diff/files`
- `GET /api/v1/forge/sessions/{session_id}/diff`

Use diff endpoints when you want fast post-run inspection without reading the entire transcript.

## 14. Chronicles and timeline

Chronicles are the durable narrative summary of a session.

### Core chronicle endpoints

| Method | Path |
|---|---|
| `GET` | `/api/v1/forge/chronicles` |
| `POST` | `/api/v1/forge/chronicles` |
| `GET` | `/api/v1/forge/chronicles/{id}` |
| `PATCH` | `/api/v1/forge/chronicles/{id}` |
| `DELETE` | `/api/v1/forge/chronicles/{id}` |
| `POST` | `/api/v1/forge/chronicles/{id}/reforge` |
| `GET` | `/api/v1/forge/chronicles/{id}/chain` |
| `GET` | `/api/v1/forge/sessions/{session_id}/chronicle` |

### Chronicle fields that matter to a controller

- `summary`
- `key_changes`
- `unfinished_work`
- `repo`
- `branch`
- `model`
- `token_usage`
- `cost`
- `duration_seconds`
- `parent_chronicle_id`

Recommended use:

- use the chronicle as the final human-facing summary
- use `unfinished_work` to seed retry or follow-up tasks
- use the reforge chain to trace lineage across retries

### Timeline endpoints

| Method | Path |
|---|---|
| `GET` | `/api/v1/forge/chronicles/{session_id}/timeline` |
| `POST` | `/api/v1/forge/chronicles/{session_id}/timeline` |

Use timeline data for:

- understanding what happened without parsing the full transcript
- building agent scorecards
- classifying sessions as coding, review, research, or failure-heavy

## 15. How to decide whether a session has finished its work

This is the most important section.

Do not rely on a single signal.

A Tyr-style controller should combine:

- session activity
- session status
- conversation evidence
- optional PR evidence
- optional CI evidence
- chronicle availability

### Signals available

#### Hard failure signals

- `session_updated` with `status=failed`
- `session_updated` with terminal error details
- live WebSocket emits `error` and the session later stops or fails

#### Hard runtime-terminal signals

- `status=stopped`
- `status=archived`

These mean the runtime is over, but they do not by themselves prove the work succeeded.

#### Strong completion signals

- `session_activity.state == "idle"`
- `activity_metadata.turn_count >= 1`
- the last turn ended with a `result`
- the session transcript contains a substantive assistant answer
- a chronicle exists

#### Optional product-delivery signals

- PR exists
- CI checks passed
- commit history changed
- diff is non-empty

### The current Tyr heuristic

The existing Tyr activity subscriber effectively does this:

1. wait for `session_activity.state == idle`
2. debounce briefly
3. require that the session processed at least one turn
4. optionally require a PR
5. optionally require CI
6. increase confidence if the session has been idle beyond a threshold
7. fetch chronicle summary on completion

That is a good baseline for OpenClaw too.

### Recommended completion algorithm

Use this algorithm:

1. If session status becomes `failed`, mark the run failed.
2. If session status becomes `stopped` before any useful work appears, mark it failed or abandoned.
3. On `session_activity=idle`, wait 5 to 10 seconds before deciding.
4. Re-fetch the session object.
5. Re-fetch conversation history.
6. Check whether at least one user turn and one assistant turn exist.
7. Inspect `activity_metadata.turn_count` if present.
8. Optionally inspect PR status.
9. Optionally inspect event history for `tool_use`, `file_modified`, `git_commit`, or `message_assistant`.
10. If the run is a normal single-agent coding session and the evidence is sufficient, mark it complete.
11. Fetch the session chronicle and store it as the durable outcome.

### Recommended confidence model

Use a weighted confidence score rather than a boolean-only decision.

Example:

- base completion after idle plus at least one turn: `0.5`
- PR exists: `+0.2`
- CI passed: `+0.2`
- extended idle: `+0.1`

Cap at `1.0`.

Then choose your orchestration action:

- `>= 0.8`: completed
- `0.5 - 0.79`: likely completed, but review transcript or chronicle
- `< 0.5`: not enough evidence

### Example completion pseudocode

```python
async def evaluate_completion(api, session_id, latest_activity):
    session = await api.get_session(session_id)

    if session["status"] == "failed":
        return {"state": "failed", "confidence": 1.0}

    if latest_activity and latest_activity.get("state") == "idle":
        turns = latest_activity.get("metadata", {}).get("turn_count", 0)
        idle_seconds = latest_activity.get("metadata", {}).get("duration_seconds", 0)

        has_turns = turns >= 1
        confidence = 0.5 if has_turns else 0.0

        pr = await api.try_get_pr_status(session_id)
        if pr and pr.get("number"):
            confidence += 0.2
            checks = pr.get("checks", [])
            if checks and all(c.get("status") in {"SUCCESS", "PASSED"} for c in checks):
                confidence += 0.2

        if idle_seconds > 30:
            confidence += 0.1

        confidence = min(confidence, 1.0)

        if confidence >= 0.8:
            chronicle = await api.try_get_session_chronicle(session_id)
            return {
                "state": "completed",
                "confidence": confidence,
                "chronicle": chronicle,
                "pr": pr,
            }

        return {"state": "idle_but_unconfirmed", "confidence": confidence}

    if session["status"] == "stopped":
        chronicle = await api.try_get_session_chronicle(session_id)
        if chronicle:
            return {"state": "stopped_with_chronicle", "confidence": 0.6}
        return {"state": "stopped_without_clear_outcome", "confidence": 0.2}

    return {"state": "running", "confidence": 0.0}
```

## 16. Special case: flock or workflow sessions

Be careful with:

- `workload_type == "ravn_flock"`

For flock-style sessions, do not assume `idle` means the entire workflow is complete.

Use a stricter rule:

- wait for explicit workflow outcome signals
- inspect room events and resulting chronicles
- treat the session as a coordinator rather than a single worker

If you are binding OpenClaw to this system, model flock sessions separately from ordinary coding sessions.

## 17. Recommended controller state machine

Use this state machine in your orchestrator:

```text
NEW
  -> CREATED
  -> WAITING_FOR_RUNNING
  -> LIVE
  -> IDLE_PENDING_EVAL
  -> COMPLETED
  -> FAILED
  -> ABANDONED
```

Meaning:

- `NEW`: task exists but no session yet
- `CREATED`: session POST succeeded
- `WAITING_FOR_RUNNING`: session not interactive yet
- `LIVE`: session running and chat transport available
- `IDLE_PENDING_EVAL`: saw idle, waiting to confirm completion
- `COMPLETED`: finished with sufficient evidence
- `FAILED`: terminal runtime or outcome failure
- `ABANDONED`: operator stopped it or evidence is too weak

## 18. Recommended runtime loop

A robust loop looks like this:

1. Create session.
2. Subscribe to the global session SSE stream.
3. Wait for `session_updated` with `status=running`.
4. Open the live WebSocket at `chat_endpoint`.
5. Send the initial instruction.
6. Accumulate assistant streaming output from `assistant` and `content_block_delta`.
7. On `result`, persist the completed turn.
8. Watch for `session_activity`.
9. When `session_activity=idle`, debounce, then evaluate completion.
10. If incomplete, send another message or escalate.
11. If complete, fetch chronicle, transcript, events, diff, and PR status.
12. Stop or archive the session if your operating policy requires it.

## 19. Recovery rules

### SSE disconnect

- reconnect
- refresh all tracked sessions with `GET /sessions/{id}`
- do not assume missed events can be replayed from SSE

### WebSocket disconnect while session still running

- reconnect to `chat_endpoint`
- fetch `/sessions/{id}/conversation`
- rebuild in-memory turn state from history

### Session stops unexpectedly

- fetch transcript
- fetch chronicle
- fetch event history
- classify as completed, failed, or abandoned based on evidence

### Missing `chat_endpoint`

Interpret this as:

- session is not yet running
- session already stopped
- or provisioning failed

Do not try to chat until `chat_endpoint` is present.

## 20. What to persist in OpenClaw

For each tracked session, persist:

- `session_id`
- current `status`
- latest `session_updated` payload
- latest `session_activity` payload
- current assistant turn buffer
- final transcript summary
- chronicle ID and summary
- PR URL and status
- completion confidence
- orchestration state

This allows crash recovery without depending on SSE durability.

## 21. Minimum viable implementation

If you want the smallest useful controller:

1. `POST /api/v1/forge/sessions`
2. `GET /api/v1/forge/sessions/stream`
3. wait for `status=running`
4. connect to `chat_endpoint`
5. send `{"type":"user","content":"..."}`
6. read until `result`
7. watch for `session_activity=idle`
8. fetch `/api/v1/forge/sessions/{id}/conversation`
9. fetch `/api/v1/forge/sessions/{id}/chronicle`
10. optionally fetch `/api/v1/volundr/sessions/{id}/pr`

That is enough to implement a basic Tyr-like controller loop.

## 22. Recommended full implementation

For production behavior, support all of:

- session lifecycle APIs
- global SSE subscription
- direct live WebSocket consumption
- HTTP message proxy fallback
- conversation fallback
- transcript and archive retrieval
- event history queries
- chronicle and timeline retrieval
- PR and diff inspection
- reconnection and rehydration logic
- confidence-based completion

## 23. Final guidance for an AI orchestrator

If you are the controller:

- treat SSE as your trigger bus
- treat the session API as your source of durable state
- treat the chat WebSocket as your live execution stream
- treat chronicles as your final summary surface
- treat completion as an evidence problem, not a single-event problem

The most reliable pattern is:

> use SSE to know when to look,
> use HTTP to confirm facts,
> use WebSocket to observe the live turn,
> use chronicles and transcript to close the loop.
