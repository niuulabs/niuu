# Forge session descriptions

Epic LEX-1262 "Session metadata", server ticket LEX-1264. A long-running session can
do many things, and the session list alone does not say which. Every Forge session
carries two text fields that its agent keeps current and that every client shows.
Feature flag: `session_descriptions: true` on `GET /api/v1/forge/feature-flags`.

## Fields

Every session record (`GET /api/v1/forge/sessions`, `GET /sessions/{id}`, the
`POST /sessions` response) and every `session_created` / `session_updated` frame on
the fleet SSE stream (`GET /sessions/stream`) carries all four keys, `null` when unset:

| Key | Type | Rule |
|---|---|---|
| `short_description` | string \| null | One line of plain text: what the session is doing now and its goal. Whitespace and newlines collapse to single spaces; truncated to 140 characters. |
| `description` | string \| null | Free-form status, markdown allowed. Stripped; truncated to 4,000 characters. |
| `short_description_updated_at` | RFC 3339 \| null | Moves only when the stored `short_description` changes value. |
| `description_updated_at` | RFC 3339 \| null | Moves only when the stored `description` changes value. |

A blank value (empty or whitespace only) is stored as `null`. Writing the value that
is already stored changes nothing: no stamp moves, `updated_at` stays, and no
`session_updated` frame is sent.

Clients: a key present in a frame replaces the value (`null` clears it); a missing key
keeps the prior value. Live updates ride **only** on the fleet `session_updated` SSE
frame, not on the per-session WebSocket.

## Writing

Three writers. All of them end at the same route.

1. **REST** `PUT /api/v1/forge/sessions/{id}/description`

   ```json
   {"short_description": "Wiring the route; tests next", "description": null}
   ```

   Partial: a key that is sent replaces the stored value, `null` clears it, an omitted
   key is unchanged. Unknown keys and non-string values are 422. Returns the full
   session record. Allowed for operators and for the session's own Forge session token
   (`forge:notify`, bound to its own session); the token is still refused on
   `PUT /sessions/{id}` and on another session's description (403). The Guild facade
   forwards the body to the owning node unchanged and answers 501 when that node
   predates the route. `POST /sessions` accepts the same two keys to seed them; the
   stamps are then the session's `created_at`.

2. **Forge MCP** tool `describe_session({short_description?, description?})`, offered
   to every session with no extra grant, over both the broker's stdio server and the
   Forge-hosted HTTP endpoint (with a session credential). It describes the caller's
   own session only. `get_session` / `list_sessions` summaries include the fields when
   set.

3. **Shell** `forge-describe --short "…" [--long "…" | --long-file <path|->]`, a PATH
   shim installed beside `present-file` for every engine (Claude, Codex, …). It posts
   to the broker's loopback Forge MCP route (`FORGE_DESCRIBE_URL`, exported when the
   Forge MCP is enabled), so the broker's session credential authorizes the write. An
   empty value clears that field. Exit codes: 2 usage, 3 not available, 1 refused.

The MCP server instructions, the session capability text and the `forge-notify` skill
tell agents to set `short_description` when they start, whenever the phase changes,
when blocked ("Blocked on …") and when done ("Done: …"), and to keep the fuller status
in `description`.

## Storage

Migration `000092_session_descriptions` adds four nullable columns to `sessions`
(Thor applied the same SQL as `000087_session_descriptions` before upstream took
that number; `migrations/lineage-aliases.json` adopts it).
Only `SessionRepository.update_description` writes them, in one statement that
compares each sent value with the stored one. The generic `update` (lifecycle,
activity, usage, archive, …) never writes them and returns the stored values, and the
paths that publish `session_updated` publish that stored row, so a stale in-memory
`Session` can neither overwrite nor briefly revert a fresher description.
