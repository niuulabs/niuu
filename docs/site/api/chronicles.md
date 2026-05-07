# Chronicles API

Chronicles capture session history — what happened, what changed, and what was left unfinished. They support reforging (relaunching from a previous session's state) and form chains when sessions build on each other.

All endpoints are prefixed with `/api/v1/forge`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chronicles` | List chronicles with filters |
| `POST` | `/chronicles` | Create from current session state |
| `GET` | `/chronicles/{id}` | Get a chronicle |
| `PATCH` | `/chronicles/{id}` | Update mutable fields |
| `DELETE` | `/chronicles/{id}` | Delete a chronicle |
| `POST` | `/chronicles/{id}/reforge` | Relaunch session from chronicle |
| `GET` | `/chronicles/{id}/chain` | Get full reforge chain |

## Timeline

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chronicles/{session_id}/timeline` | Get event timeline |
| `POST` | `/chronicles/{session_id}/timeline` | Add timeline event |
| `GET` | `/chronicles/{session_id}/diff` | Get file diff |

## Chronicle model

```json
{
  "id": "uuid",
  "session_id": "uuid",
  "status": "draft|complete",
  "project": "my-project",
  "repo": "github.com/org/repo",
  "branch": "feature-branch",
  "model": "claude-sonnet-4-20250514",
  "summary": "Fixed authentication flow...",
  "key_changes": ["Updated auth middleware", "Added token refresh"],
  "unfinished_work": "Need to add tests for...",
  "token_usage": 45000,
  "cost": "1.35",
  "duration_seconds": 1800,
  "tags": ["auth", "backend"],
  "parent_chronicle_id": "uuid or null"
}
```

## Timeline events

Timeline events track granular activity within a session:

| Type | Description |
|------|-------------|
| `session` | Session start/stop |
| `message` | User or assistant message |
| `file` | File created/modified/deleted |
| `git` | Commit, push, branch |
| `terminal` | Terminal command execution |
| `error` | Error occurrence |

Each event has a `t` field (seconds since session start) for timeline positioning.

## Reforging

Reforge creates a new session pre-configured from a chronicle's state:

```
POST /chronicles/{id}/reforge
```

This creates a new session with the same repo, branch, model, and config, and links the new chronicle to the parent via `parent_chronicle_id`. The chain endpoint returns the full ancestry.
