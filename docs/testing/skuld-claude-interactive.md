# Skuld Claude Interactive Test Plan

`skuldClaudeInteractive` runs Claude Code as an interactive tmux session via
`skuld.transports.tmux_interactive.TmuxInteractiveTransport`. It is intentionally
opt-in; the existing `skuldClaude` runtime remains the SDK/stream-json default.

## Automated Coverage

Run the focused backend suite:

```bash
uv run pytest \
  tests/test_skuld/test_tmux_interactive_transport.py \
  tests/test_skuld/test_config.py::TestTransportAdapter \
  tests/test_skuld/test_transport.py::TestTransportCapabilities \
  tests/test_skuld/test_broker.py::TestDispatchBrowserMessage \
  tests/test_volundr/test_session_definition_contributor.py::TestDefaultSessionDefinitions \
  tests/test_charts/test_volundr_chart.py::TestValuesDefaults
```

Run the real tmux smoke test without Claude credentials:

```bash
uv run pytest -m integration \
  tests/test_skuld/test_tmux_interactive_transport.py::test_real_tmux_smoke_with_fake_claude
```

The fake-runner tests validate command construction, pane discovery, pane
capture events, paste-buffer input, key dispatch, resize dispatch, interrupt,
capabilities, and synthetic result closure. The integration smoke uses a fake
`claude` executable but real tmux to verify the pipe-pane/tail loop end to end.

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
3. Send a normal chat message and verify:
   - `terminal_input_sent` is emitted.
   - `terminal_output` frames are persisted.
   - a conservative `assistant` / `content_block_delta` / `result` sequence is
     emitted after terminal output goes idle.
4. Send `{"type":"slash_command","command":"status"}` and verify `/status`
   runs in the interactive Claude terminal.
5. Send `terminal_key` controls for `Up`, `Down`, `Escape`, and `C-c`; verify
   the terminal reflects history/menu/cancel behavior.
6. Send `terminal_resize` with a desktop and mobile-size geometry; verify tmux
   accepts the resize and emits `terminal_resized`.
7. If agent teams are enabled for the session, run `/agents` and verify new tmux
   panes appear as `terminal_pane_opened` events with independent output logs.
8. POST a sample payload to `/api/claude/hooks`; verify a `claude_hook` event is
   appended to the session event log.

## Known Fidelity Boundary

This transport gives interactive-command parity, not stream-json parity. Tmux
captures terminal bytes and Claude hooks can supply structured lifecycle events,
but hidden model state, exact token accounting, and every SDK-level delta are
not guaranteed. Keep SDK-based `skuldClaude` for structured automation that
needs exact stream-json semantics.
