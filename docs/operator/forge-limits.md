# Forge limits

Every limit Forge itself enforces: what really bounds that resource, and what happens
when the limit is reached. These follow the rule in `.claude/rules/no-hidden-limits.md`:

- each limit is a config knob, not a literal in the code;
- hitting it raises an error that names the knob and the remedy;
- each hit is counted where an operator looks.

Ravn's limits are in [ravn-limits.md](ravn-limits.md).

| Limit | Default | Real bound | When it is hit |
|---|---|---|---|
| `presented_files.max_bytes` | 50 MiB | Size of one file a session hands over (matches Skuld's `max_presented_file_bytes`) | The upload is refused with 413, and `present-file` fails |
| `presented_files.max_total_bytes` | **none: required when `presented_files.enabled`** | The disk under `presented_files.store.root`, which only the operator knows. Copies are kept until their session is deleted. | Forge refuses the copy with HTTP 507, `detail.code: presented_files_quota`, and a message naming the knob and the remedy: raise it, or delete sessions with presented files. `present-file` exits non-zero with that message, and nothing is presented. The refusal is logged at error level and counted in `presented_files.quota_refusals`, next to `used_bytes` and `max_total_bytes`, in `GET /api/v1/forge/health`. Nothing is ever evicted to make room. (LEX-1514) |
| `tool_result_preview.min_edge` / `max_edge` | 320 / 1600 px | The Pillow work and cached bytes for one preview; beyond 1600 px, fetch the full image | The request gets 422, naming the bounds (LEX-1655) |
| `SKULD__TMUX_TURN_MAX_SECONDS` (Skuld) | 3600 s | How long a turn may run without a completion signal (a `Stop` hook, or idle-prompt evidence) before Forge stops waiting | The turn ends with `stop_reason: timeout` and keeps the text it displayed; it is not marked as an error. The hit is logged at error level and recorded as a `system` frame `turn_cap_reached` (LEX-1670) |

## Suggested `presented_files.max_total_bytes`

| Host | Value | Why |
|---|---|---|
| mini-m6 | `5368709120` (5 GiB) | The disk is about 91 % full, with about 40 GiB free |
| Thor | `53687091200` (50 GiB) | Plenty of free disk |
