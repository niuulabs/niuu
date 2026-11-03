# Skuld Claude Interactive Test Plan

`skuldClaudeInteractive` runs Claude Code as an interactive tmux session via
`skuld.transports.tmux_interactive.TmuxInteractiveTransport`. It is intentionally
opt-in; the existing `skuldClaude` runtime remains the SDK/stream-json default.

## Automated Coverage

Run the focused backend suite:

```bash
uv run pytest \
  tests/test_skuld/test_tmux_interactive_transport.py \
  tests/test_skuld/test_tmux_workspace_trust.py \
  tests/test_skuld/test_config.py::TestTransportAdapter \
  tests/test_skuld/test_transport.py::TestTransportCapabilities \
  tests/test_skuld/test_broker.py::TestDispatchBrowserMessage \
  tests/test_volundr/test_session_definition_contributor.py::TestDefaultSessionDefinitions \
  tests/test_charts/test_volundr_chart.py::TestValuesDefaults
```

Run the real tmux smoke test without Claude credentials:

```bash
uv run pytest -m integration \
  tests/test_skuld/test_tmux_interactive_transport.py::test_real_tmux_smoke_with_fake_claude \
  tests/test_skuld/test_tmux_workspace_trust.py::test_real_tmux_workspace_trust_then_chat
```

The fake-runner tests validate command construction, pane discovery, pane
capture events, paste-buffer input, key dispatch, resize dispatch, interrupt,
capabilities, and synthetic result closure. The integration smoke uses a fake
`claude` executable but real tmux to verify the pipe-pane/tail loop end to end.

## Workspace trust at startup

The interactive transport monitors the first page on every launch or attachment,
including sessions without an initial prompt. When Claude shows its workspace
trust menu for the configured workspace, it moves the selection from **No, exit**
to **Yes, I trust this folder**, verifies the changed highlight, and presses Enter
once. If Yes is already selected, no arrow key is needed. This is independent of
Claude's tool-permission mode; other permission and login prompts remain separate.

The displayed folder must resolve to the configured workspace. Missing or partially
rendered menu rows are observed until the existing startup deadline; a different
folder requires terminal review. Repeated captures cannot repeat the arrow or
confirmation, including after an uncertain key-send failure. A new native process
gets its own confirmation state.

Startup and chat/discovery share the input lock. Chat is delivered only after the
normal prompt appears; a timeout reports a delivery failure with the input unsent,
instead of pasting into the startup screen. The existing
`SKULD__TMUX_REPL_READY_TIMEOUT_SECONDS` controls the deadline (12 seconds by default).
If a hook-backed question or permission request arrives during startup, attachment
can finish so the existing question UI can answer it. This is a waiting-for-answer
state, not chat readiness: chat remains blocked and any seed stays unsent until
startup is retried after the question resolves.
The tests cover delayed rendering, both menu orders, an existing selection,
concurrent startup/chat, attachment, stuck screens, mismatched folders, and a
login menu before or after trust. A selection chevron followed by an option is
never a ready prompt, including while the trust screen is partially rendered.
An explicit `SKULD__TMUX_REPL_READY_MARKER` remains required after confirmation
when configured. The real tmux test uses an isolated fake CLI with a working
trust widget and verifies the subsequent chat result; it does not call Claude.

Existing running gateways keep their loaded transport code. Deploying the change
affects gateways started from the updated source; adopting it in a running session
requires that session's normal controlled restart.

## A turn with no completion signal (LEX-1670)

A tmux turn normally ends with Claude's `Stop` hook, or with the idle-prompt
recovery when a `Stop` is missing. `SKULD__TMUX_TURN_MAX_SECONDS` (3600 by default)
bounds how long the transport waits for either. Time spent waiting on a question or
permission menu does not count toward it.

A turn can pass that bound and still be healthy. Two cases have been seen:

- The main agent replies and then waits on background agents. Claude sends no `Stop`
  while it waits.
- A `Stop` is not attributed to the main pane. Brokers built before #1072 failed to
  correlate long pasted prompts. After `/clear` changed Claude's session id, such a
  broker treated every main-pane hook as a sub-agent's.

When the bound is reached, the turn ends with `stop_reason: timeout` and
`is_error: false`. It carries the last message it displayed (MessageDisplay), or
else the reply the terminal shows. It is never an empty error result: that used to
replace the reply in the Forge conversation with "The agent stopped with an error."

The hit is loud in two ways:

- an error-level log line naming the knob;
- a durable `system` frame with `subtype: turn_cap_reached`, plus `knob`,
  `turn_max_seconds` and `had_text`, which you can count in the session log.

If a `Stop` arrives later with the same text, it is dropped as an echo. If it
carries new text, that text is recorded as well. Only a `StopFailure` (an API error)
or the process exiting still ends a turn as an error.

### Stale tmux sessions are never adopted

Claude reads its hook URL and secret once, when it launches, and the secret is new
for every broker start. A tmux session left by an earlier broker, for example a stop
that did not finish or a broker restarted after a Forge switch, keeps posting hooks to
that broker. Its hooks are refused (ios-fixes got HTTP 401 on 2026-10-30), a `Stop`
never ends the turn, and the reply never reaches Forge.

Each session the transport creates is stamped with the tmux option
`@skuld_hook_binding`, a fingerprint of its hook URL and secret. At start, a broker
adopts an existing session only if that session carries its own stamp. Otherwise it
ends the session (gracefully first, when remote control is on) and starts a fresh
Claude, which resumes the conversation. The replacement is logged at warning level
and recorded as a `system` frame with `subtype: stale_tmux_session_replaced`.

Sessions created before this release carry no stamp, so they are replaced the first
time a new broker starts for them.

### When Claude's pane dies (LEX-1699)

The tmux session keeps a dead pane (`remain-on-exit`). A teammate's dead pane is just
closed. When Claude's own pane (`main.0`) dies:

1. The transport reports `transport_stopped` once, with `reason: claude_exited` and the
   pane's exit status, and logs at error level.
2. The broker reports activity `stopped` with the same reason and status, then exits
   gracefully, which flushes its event log.
3. The pod manager sees the exit and Forge reconciles the session as stopped, within
   one pane poll plus the reconcile.

From then on:

- a message to the session is refused (no live pod: 409), instead of being accepted
  as `pending` for a broker with no agent;
- `start` launches a new broker. That broker never adopts the old tmux session: a
  dead primary pane, or a stamp from another broker, makes it end the session and
  start a fresh Claude, which resumes the conversation.

`exit_when_transport_stops: false` (Skuld setting) keeps the broker up, serving
history only. The test harness uses that because it hosts the broker in-process.
Seen live on 2026-10-30: ios-speaker-bargein's pane was dead for 8 hours while Forge
reported it running and queued its messages.

### The stale-socket sweep spares new sockets

Every broker sweeps the shared tmux socket directory and unlinks sockets whose
server no longer answers. A sibling's server that is still starting fails that
probe for a moment. On 2026-10-30, six lanes started together on the mini and
ios-fixes lost its live socket that way: its broker could no longer reach its pane
and stayed in `provisioning`.

The sweep now spares two kinds of socket:

- one younger than `SKULD__TMUX_SOCKET_SWEEP_MIN_AGE_SECONDS` (600 s by default);
- the broker's own socket.

A server still unreachable after that age is dead, and the next sweep removes it.

General terminal choices without structured Claude hooks are deferred; see the
[future terminal-question requirement](../forge/claude-terminal-questions-future.md).

## Live Claude Smoke

Prerequisites:

- `tmux` is installed in the Skuld image or local runtime.
- Claude Code is logged in through the subscription path. For local testing,
  run `claude /status` and verify it is not using an API key unless that is the
  intended billing mode.
- `SKULD__CLAUDE_AUTH` is unset or set to `subscription`.

Manual checks:

1. Launch a session with definition `skuldClaudeInteractive`.
2. Connect to the session WebSocket and confirm capabilities include
   `terminal_output`, `terminal_input`, `terminal_keys`, `terminal_resize`,
   `terminal_panes`, `slash_commands`, and `interrupt`.
3. Fetch `GET /api/slash-commands` and verify the response includes live
   commands from Claude Code's `/` autocomplete menu, including custom
   project/user commands and workflow commands such as `/workflows` when
   available.
4. Send `{"type":"discover_slash_commands","refresh":true}` over the session
   WebSocket and verify the response is `{"type":"slash_commands", ...}`.
5. Send `POST /api/slash-commands/send` with `{"command":"workflows"}` or send
   `{"type":"slash_command","command":"workflows"}` over the WebSocket; verify
   the command is injected as terminal input rather than as a chat turn.
6. Send a normal chat message and verify:
   - `terminal_input_sent` is emitted.
   - `terminal_output` frames are persisted.
   - a conservative `assistant` / `content_block_delta` / `result` sequence is
     emitted after terminal output goes idle.
7. Send `terminal_key` controls for `Up`, `Down`, `Escape`, and `C-c`; verify
   the terminal reflects history/menu/cancel behavior.
8. Send `terminal_resize` with a desktop and mobile-size geometry; verify tmux
   accepts the resize and emits `terminal_resized`.
9. If agent teams are enabled for the session, run `/agents` and verify new tmux
   panes appear as `terminal_pane_opened` events with independent output logs.
10. POST a sample payload to `/api/claude/hooks`; verify a `claude_hook` event is
   appended to the session event log.

## Known Fidelity Boundary

This transport gives interactive-command parity, not stream-json parity. Tmux
captures terminal bytes and Claude hooks can supply structured lifecycle events,
but hidden model state, exact token accounting, and every SDK-level delta are
not guaranteed. Keep SDK-based `skuldClaude` for structured automation that
needs exact stream-json semantics.
