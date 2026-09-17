# Web session names and slash commands

Reviewed 2026-09-17 on `forge/ux-improvement`.

## Rename

The pencil in the session header and the sidebar action overlay opens the same
popover. Save (or Enter) calls `IVolundrService.updateSession`, then updates and
invalidates the raw session, domain session and session list caches. Cancel and
Escape discard the draft. Errors retain it for another attempt. On touch devices,
the sidebar's existing More button reveals the pencil alongside the lifecycle
actions. Read-only detail views omit the pencil.

The actual endpoint is `PUT /api/v1/forge/sessions/{id}` with `{ "name": "new-name" }`.
The aggregate routes the request to the owning Forge. The previous, unused web
adapter sent PATCH; this change corrects it to PUT.

The running server's `SessionUpdate` contract requires a lowercase DNS label of
1–63 characters: letters, digits and internal hyphens. The popup validates that
contract, with no silent conversion of the user's text. Human-readable names with
spaces require a separate backend contract change; the domain currently allows
255 characters but the REST update model does not. No runtime restart is needed
for the web controls.

## Skuld API

For the live/source catalogue gap and the proposed expansion sequence, see the
[slash-command coverage review](web-slash-command-coverage-review.md).

These are session broker endpoints, reached using that session's `chat_endpoint`
and its `/s/{session-id}/api/…` HTTP proxy. They do not depend on the separate
Terminal tab or terminal daemon.

| Operation           | API                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------- |
| Capabilities        | `GET …/api/capabilities`; WebSocket `capabilities` frame                                 |
| Cached catalogue    | `GET …/api/slash-commands?refresh=false`                                                 |
| WebSocket discovery | `{ "type": "discover_slash_commands", "refresh": false }`                                |
| Catalogue frames    | `available_commands` or `slash_commands`; rich `commands` entries                        |
| WebSocket execution | `{ "type": "slash_command", "command": "/review", "arguments": "…", "request_id": "…" }` |
| HTTP execution      | `POST …/api/slash-commands/send` with `command` and `arguments`                          |

Sources: `src/skuld/broker_api.py`, `broker.py`, `slash_commands.py`, and the
`tmux_interactive`, `sdk`, `persistent_subprocess` and `codex_ws` transports.
Lexi iOS's `ForgeKit/FKSlashCommand.swift` supplies the reference behavior:
session-scoped catalogues, rich entries taking precedence over legacy name lists,
descriptions and argument hints, and autocomplete only before argument whitespace.

The shared React composer now exposes a `/` button. Type `/` to browse or filter,
use arrows to navigate, and Enter/Tab or a click to insert. Add arguments and send
explicitly. Existing drafts are preserved. Entries come from the active session,
not a hardcoded union of Claude and Codex commands. Unknown slash-prefixed text
retains the ordinary message path; advertised commands without attachments use
Skuld's control path and preserve argument whitespace.

Normal discovery uses `refresh=false`: a forced tmux discovery can open Claude's
interactive autocomplete and must not happen merely because someone opens a web
session. The existing HTTP lookup covers a missing WebSocket catalogue after
1.5 seconds; it is aborted when discovery resolves or the session changes.
Reconnects request a fresh cached catalogue. Command errors display the broker's
reason without ending an unrelated active response, and execution is never retried
automatically.

Thor's running Codex broker was checked read-only and reported slash support with
`/effort`, `/compact`, `/review`, `/goal`, `/title`, and `/fork`. Other session
runtimes may advertise different sets. No live session was renamed or sent a
command during verification. Fixture tests cover rename persistence, native command
dispatch, and rejection handling; live browser checks cover presentation only.

Some native commands open terminal-only interactive menus. Discovery does not imply
that those menus have equivalent React controls. Dedicated web controls for such
flows remain a separate integration task.
