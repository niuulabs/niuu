# Sessions API

All endpoints are prefixed with `/api/v1/forge`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions` | List sessions with optional status filter |
| `GET` | `/sessions/stream` | SSE stream for real-time updates |
| `POST` | `/sessions` | Create and start a new session |
| `POST` | `/sessions/archive-stopped` | Bulk archive all stopped sessions |
| `GET` | `/sessions/{id}` | Get a session by ID |
| `PUT` | `/sessions/{id}` | Update session (name, model, branch, tracker issue) |
| `DELETE` | `/sessions/{id}` | Delete a session and its pods |
| `POST` | `/sessions/{id}/start` | Restart a stopped session |
| `POST` | `/sessions/{id}/stop` | Stop a running session |
| `PATCH` | `/sessions/{id}/archive` | Archive a session |
| `PATCH` | `/sessions/{id}/restore` | Restore an archived session |
| `POST` | `/sessions/{id}/usage` | Report token usage |
| `GET` | `/sessions/{id}/logs` | Proxy logs from the session pod |
| `GET` | `/sessions/{id}/diff` | Get git diff from session workspace |
| `GET` | `/sessions/{id}/events` | Query session events |
| `GET` | `/sessions/{id}/events/counts` | Event type counts |
| `GET` | `/sessions/{id}/events/tokens` | Token burn timeline |
| `GET` | `/sessions/{id}/chronicle` | Most recent chronicle for session |

## Session lifecycle

```
CREATED → STARTING → PROVISIONING → RUNNING → STOPPING → STOPPED → ARCHIVED
                                         └──→ FAILED
```

- `CREATED` — session record exists, no pods running
- `STARTING` — pod creation requested
- `PROVISIONING` — pods created, waiting for readiness
- `RUNNING` — pods ready, endpoints available
- `STOPPING` — stop requested, pods being torn down
- `STOPPED` — pods removed, session data retained
- `FAILED` — infrastructure error
- `ARCHIVED` — soft-deleted, can be restored

## Create session

```
POST /sessions
```

```json
{
  "name": "fix-auth-bug",
  "model": "claude-sonnet-4-20250514",
  "repo": "github.com/org/repo",
  "branch": "main",
  "template_name": "python-project",
  "preset_id": "uuid"
}
```

Creates the session record and immediately starts pod provisioning. Returns the session with status `STARTING`.

## SSE stream

```
GET /sessions/stream
```

Returns a Server-Sent Events stream with real-time updates:

- `session_created` — new session created
- `session_updated` — session state changed
- `session_deleted` — session deleted
- `stats_updated` — aggregate stats refreshed (every 30s)
- `heartbeat` — keepalive (every 30s)
- `chronicle_created` / `chronicle_updated` / `chronicle_deleted`
- `pr_created` / `pr_merged`

## Workspaces

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workspaces` | List current user's workspaces |
| `GET` | `/workspaces/{id}` | Get a workspace |
| `POST` | `/workspaces/{id}/restore` | Restore archived workspace |
| `DELETE` | `/workspaces/{id}` | Delete workspace and storage |
| `GET` | `/admin/workspaces` | List all workspaces (admin) |

## Models and stats

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/models` | List available LLM models with pricing |
| `GET` | `/stats` | Aggregate statistics (sessions, tokens, cost) |
