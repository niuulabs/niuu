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
The tests cover delayed rendering, both menu orders, an existing selection,
concurrent startup/chat, attachment, stuck screens, mismatched folders, and a
following login menu. The real tmux test uses an isolated fake CLI with a working
trust widget and verifies the subsequent chat result; it does not call Claude.

Existing running gateways keep their loaded transport code. Deploying the change
affects gateways started from the updated source; adopting it in a running session
requires that session's normal controlled restart.

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
