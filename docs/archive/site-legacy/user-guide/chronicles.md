# Chronicles

Chronicles capture the full history of a session. When a session stops, Volundr auto-creates a chronicle with everything that happened.

## What a chronicle contains

**Summary** — An AI-generated description of the session: what was accomplished, what approach was taken.

**Key changes** — A list of the significant modifications made during the session.

**Unfinished work** — Tasks the agent started but did not complete. Useful when reforging a session to continue where it left off.

**Timeline** — Every event that occurred, ordered by time:

- Message exchanges (with token counts)
- File edits (insertions and deletions per file)
- Git commits (hash, message, timestamp)
- Terminal commands (with exit codes)
- Errors and warnings

**Config snapshot** — The session's configuration at the time: model, repo, branch, resource limits.

**Token usage** — Total tokens consumed during the session.

## Chronicle chains

When you reforge a session (create a new session from a chronicle), the new session's chronicle links back via `parent_chronicle_id`. This creates a traceable chain of work across sessions.

```
Chronicle A (session 1)
  └── Chronicle B (session 2, reforged from A)
       └── Chronicle C (session 3, reforged from B)
```

Each chronicle in the chain knows its parent. You can walk the chain to see the full history of a long-running task that spanned multiple sessions.

## Browsing chronicles

**Web UI** — Open the Chronicles tab to browse, search, and filter chronicles.

**API** — `GET /api/v1/forge/chronicles` returns chronicles with filtering support. Filter by project, repo, model, or tags.

**TUI** — The chronicles page shows a timeline view with a token burn graph — a cumulative chart of token usage over the session's lifetime.

## Automatic creation

Chronicles are created automatically when a session stops. You do not need to trigger this manually. The chronicle generation runs as part of the session shutdown process, so there is always a record of what happened.

## Using chronicles for reforging

The main use of chronicles beyond record-keeping is reforging. When you create a new session from a chronicle, the new session starts with the context of the previous one. The agent knows what was done, what is left, and where the code stands.

This makes it practical to break long tasks into multiple sessions without losing context.
