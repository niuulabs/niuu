# Forge limits

Every limit Forge itself enforces: what really bounds that resource, and what happens
when the limit is reached. These follow the rule in `.claude/rules/no-hidden-limits.md`:

- each limit is a config knob, not a literal in the code;
- hitting it raises an error that names the knob and the remedy;
- each hit is counted where an operator looks.

The limits of the shared helpers and the other services are in [limits.md](limits.md).

| Limit | Default | Real bound | When it is hit |
|---|---|---|---|
| `projects.team_usage_page_size` | 50 | How many uses of a project's team integrations one page shows | Older uses are paged with `before` (the response's `next_before`), never dropped |
| `presented_files.max_bytes` | 50 MiB | Size of one file a session hands over (matches Skuld's `max_presented_file_bytes`) | The upload is refused with 413, and `present-file` fails |
| `presented_files.max_total_bytes` | **none: required when `presented_files.enabled`** | The free space under `presented_files.store.root` (on a cluster, the presented-files volume): Forge refuses to start when free space plus what copies already use cannot hold it. Copies are kept until their session is deleted. On a cluster the quota is the sum of `forge_presented_files` rows, shared by every Forge replica. | Forge refuses the copy with HTTP 507, `detail.code: presented_files_quota`, and a message naming the knob and the remedy: raise it, or delete sessions with presented files. `present-file` exits non-zero with that message, and nothing is presented. The refusal is logged at error level and counted in `presented_files.quota_refusals`, next to `used_bytes` and `max_total_bytes`, in `GET /api/v1/forge/health`. Nothing is ever evicted to make room. (LEX-1514) |
| `tool_result_preview.min_edge` / `max_edge` | 320 / 1600 px | The Pillow work and cached bytes for one preview; beyond 1600 px, fetch the full image | The request gets 422, naming the bounds (LEX-1655) |
| `SKULD__TMUX_TURN_MAX_SECONDS` (Skuld) | 3600 s | How long a turn may run without a completion signal (a `Stop` hook, or idle-prompt evidence) before Forge stops waiting | The turn ends with `stop_reason: timeout` and keeps the text it displayed; it is not marked as an error. The hit is logged at error level and recorded as a `system` frame `turn_cap_reached` (LEX-1670) |
| Session voice credential (`voice.providers.<name>.kwargs.ttl_seconds` / `timeout_seconds`) | 600 s lifetime / 12 s request | `ttl_seconds`: how long the provider lets a call start on its short-lived secret (the phone connects within moments; a longer-lived secret is a longer-lived leak). `timeout_seconds`: how long the provider's `client_secrets` endpoint takes to answer. Both must be positive or the minter is refused at startup; the expiry is the provider's own (an answer without one is refused, never guessed) | A minter that times out raises `RealtimeCredentialError` (HTTP 502 to the broker) naming `timeout_seconds`; error log, counted on `ravn.cap.hits` (`voice.providers.<name>.kwargs.timeout_seconds`). An expired secret is refused by the provider and the app asks for a new one |

## Suggested `presented_files.max_total_bytes`

| Host | Value | Why |
|---|---|---|
| mini-m6 | `5368709120` (5 GiB) | The disk is about 91 % full, with about 40 GiB free |
| Thor | `53687091200` (50 GiB) | Plenty of free disk |
