# Skuld / Forge tmux — Comprehensive Test Plan

> Status: **implemented (Phases 0–4)** — see "Outcomes" at the end. Extends
> `docs/testing/skuld-claude-interactive.md` (command construction + the basic
> real-tmux smoke). This document targets the *behavioural* gaps: steering,
> interrupts, crash + reconnect, `ask_user_question` in tmux, permission modes, and
> the custom multi-agent tmux surfaces (agent selection, "running agents",
> workflows, teams).

## 1. Why this plan exists

"Claude Code in tmux" (`skuld.transports.tmux_interactive.TmuxInteractiveTransport`)
is the strategic runtime, but it is the *least* deterministically tested mode. The
hard parts — steering a live turn, recovering after a client crash, surfacing a TTY
menu as a structured `ask_user_question`, and translating a structured answer back
into keystrokes — are exactly the parts that have produced the recent bug-fix
commits (Bug 2 crash transcripts, Bug 3 inbound steering after reconnect, tmux
lock-state re-surfacing). Each fix shipped with a narrow regression test; we do not
yet have a *general* harness that exercises these flows the way a real client does.

The recurring symptoms the plan must catch:

1. **Steering / restart** — a message sent mid-turn either restarts the turn,
   gets dropped, or wedges the send lock (Bug 3).
2. **Crash + reconnect** — the client WebSocket dies mid-turn (especially while the
   agent is blocked on a question) and the session reloads empty or frozen
   (Bug 2 + tmux lock-state).
3. **`ask_user_question` in tmux** — the question is not surfaced to the frontend,
   or the answer keystroke lands in the wrong place because the menu had not
   rendered / had scrolled.
4. **Permission modes** — we run almost everything with `bypassPermissions`, so the
   "model asks to use a tool" path is barely exercised. We want to test sessions
   that are *not* all-permissions.
5. **Custom tmux surfaces** — agent selection, "which agents are running", the new
   workflow view, and teams-of-agents. These are screens reached by tmux
   navigation and have no programmatic test today.

## 2. Current coverage (baseline) and the gaps

Anchors are the files/lines a test author should read first.

| Area | What exists today | Gap |
|---|---|---|
| Command construction, pane discovery, capture, paste, key, resize, interrupt | `tests/test_skuld/test_tmux_interactive_transport.py` via `FakeTmuxInteractiveTransport` (in-memory `_run_tmux`) | Fakes the tmux command runner, so it never exercises real pane rendering, real timing, or a real agent loop |
| Real tmux plumbing | `test_real_tmux_smoke_with_fake_claude` (line 319) — a 4-line bash echo as "claude" | Fake agent is too dumb: cannot render menus, ask questions, simulate work, emit hooks, or crash. No reconnection, no questions, no permissions |
| Hook → semantic events | `test_claude_stop_hook_emits_semantic_result`, PreToolUse/PostToolUse/PermissionRequest tests | Driven by hand-fed hook payloads, not by a real agent producing them through tmux |
| Steering | `test_mid_turn_message_steers_without_stopping_turn`; `test_deliver_user_text_raises_when_send_lock_is_wedged` | Fake runner; no reconnect-then-steer (Bug 3), no steer-while-question-pending, no multi-agent target routing |
| Crash transcript rebuild | `tests/test_domain/test_transcript_rebuild.py` (pure reducer) | Reducer tested in isolation; never driven from a real broker event log produced by a crash |
| Reconnect replay | indirectly in `tests/test_skuld/test_broker.py` (`_pending_ask_user_questions`) | No end-to-end "connect → crash → reconnect → assert replay" with a live transport |
| ask_user_question (SDK / persistent subprocess) | `test_persistent_subprocess.py` HITL test; broker tracking tests | tmux TTY-bridge answer translation is only tested against a mocked capture buffer — the race the user reported is untested |
| Permission modes | `tests/test_volundr/test_permission_auto_approval.py` (policy engine) | No test of a tmux session running in `default`/`plan`/`acceptEdits` and surfacing a real permission gate to a client |
| Agent selection / running agents / workflows / teams | none | No programmatic tmux navigation of these screens at all |

### Key code anchors

- Transport port: `src/niuu/ports/cli/transport.py` (`CLITransport`, `TransportCapabilities`).
- tmux transport: `src/skuld/transports/tmux_interactive.py`
  - input delivery / steering: `send_message` → `_deliver_user_text` (~358–434)
  - control (keys/resize/answer): `send_control` (~459–531)
  - hook bridge: `handle_claude_hook` (~567–743)
  - TTY question/permission bridge: `_surface_tty_*`, `_answer_tty_prompt`,
    `_capture_menu_rows`, `_match_menu_digit` (~744–950)
  - pane watch / frames: `_watch_panes`, frame capture (~1119–1320)
  - argv (remote control, teammate mode): `_interactive_argv` (~1037–1065)
- broker: `src/skuld/broker.py`
  - browser dispatch: `_dispatch_browser_message` (~3376–3700)
  - delivery ACK (Bug 3): `_deliver_user_message_and_ack` (~3728–3800)
  - cli event fold + durable log: `_handle_cli_event` (~2919–3012), `_enqueue_event_log` (~4428)
  - websocket + reconnect replay: `handle_websocket` (~5497–5630), replay (~5590–5614)
  - attention / lock-state: `_exit_attention` (~4987), `_pending_ask_user_questions` (~1065)
- crash rebuild: `src/volundr/domain/services/transcript_rebuild.py` (`rebuild_turns`),
  `session_archive.py::_load_event_log_transcript`, `rest.py::get_conversation`.

## 3. The investment: three reusable harness components

The plan deliberately front-loads **infrastructure**. Most scenarios below are
one-screen-of-code once these three pieces exist. They live under
`tests/support/forge/` (new) so both `test_skuld` and integration suites can import them.

### 3.1 `fakeagent` — a scriptable "testing version" of Claude Code

A small, dependency-free Python CLI that *looks like* Claude Code to tmux and to the
hook bridge, but does exactly what a test script tells it to. This is the single
highest-leverage piece — it replaces the 4-line bash echo and unlocks nearly every
behavioural scenario without Claude credentials or network.

Location: `tests/support/forge/fakeagent/__main__.py` (run via `python -m fakeagent`,
installed onto `PATH` as `claude` in a tmp bin dir, same trick as the current smoke).

Requirements:

- **Deterministic REPL**: prints a banner, reads a line of input, and reacts based on
  a *directive grammar* embedded in the input or in a `FAKEAGENT_SCRIPT` env/file.
  - `work:<seconds>` → emit incremental output for N seconds (drives steering /
    interrupt / idle-timeout tests), then finish.
  - `say:<text>` → emit `text` as the assistant response.
  - `ask:<header>|<q>|<opt1>;<opt2>;...` → render a numbered `AskUserQuestion`-style
    menu in the TTY *and* fire the matching hook (see below). Block until a digit+Enter.
  - `perm:<tool>|<cmd>` → render the Allow / Allow-and-don't-ask / Deny gate and fire a
    `PermissionRequest` hook. Block until resolved.
  - `crash` / `exit:<code>` → die immediately (drives crash-recovery).
  - `tool:<name>` → fire PreToolUse/PostToolUse hooks around a no-op tool.
- **Hook emission**: when started with `--settings <hooks.json>` (as the transport
  does), POST the same hook events the real CLI would to the broker hook endpoint
  (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`). This
  lets us test the hook-driven path, not just the synthetic-turn path. A flag
  `--no-hooks` forces the pure-tmux synthetic-turn path so both code paths get coverage.
- **Remote control / teammate flags**: accept and ignore `--remote-control`,
  `--teammate-mode`, `--mcp-config`, `--model`, etc. so argv stays realistic.
- **Custom-screen fixtures**: a `screen:<name>` directive renders a static, labelled
  screen (e.g. an agent-selection list, a "running agents" table, a workflow board) so
  the tmux page-object layer (§3.2) can be tested without those features existing in
  the real product yet. Screens are plain text templates under
  `tests/support/forge/fakeagent/screens/`.

Why a fake instead of real Claude: tests must be hermetic, fast, credential-free,
and able to force rare states (crash exactly mid-turn, menu that renders 500ms late,
a question left unanswered at reconnect). Real Claude can be exercised by a separate,
manually-run live smoke (§7), but CI relies on `fakeagent`.

### 3.2 `TmuxPage` — a page-object / driver for tmux screens

A Playwright-for-tmux helper so every "page" (the REPL, a permission menu, an agent
picker, a running-agents table, a workflow view) is asserted the same way. This is the
"general way to navigate tmux pages" the request asks for.

Location: `tests/support/forge/tmux_page.py`.

API sketch:

```python
page = TmuxPage(socket=sock, session=name, target=pane_id)
await page.wait_for_text("Allow this tool?", timeout=2.0)   # capture-pane poll
rows = await page.menu_rows()                               # parsed numbered options
await page.press("2")                                       # send-keys
await page.type("a steering message")                       # load-buffer + paste + Enter
await page.snapshot()                                       # full screen text for golden assert
assert page.cursor_row, page.cols                           # geometry
```

It wraps `tmux capture-pane`, `send-keys`, `load-buffer`/`paste-buffer`, `list-panes`,
`resize-pane`. It reuses the transport's own parsing helpers where possible
(`_extract_assistant_response`, `_capture_menu_rows`, `_match_menu_digit`) so the test
asserts against the *same* parser the product ships — a divergence there is itself a bug.
For multi-pane / multi-window screens it exposes `panes()` and `select_pane(id)` /
`select_window(name)` so navigation between "pages" is first-class.

### 3.3 `BrokerHarness` — broker + WS client + durable-log spy

A fixture that boots a real `Broker` against a `TmuxInteractiveTransport` driving
`fakeagent`, exposes one or more in-process WebSocket clients, and captures the durable
event log instead of POSTing it to Volundr.

Location: `tests/support/forge/broker_harness.py`.

Provides:

- `async with BrokerHarness(script=..., hooks=True, permission_mode="default") as h:`
- `client = await h.connect()` → a fake WS channel that records every broadcast frame
  and can `send({...})` (steer, interrupt, ask_user_answer, permission_response, keys).
- `await h.drop(client)` / `await h.kill_client(client)` → simulate clean disconnect vs
  abrupt crash (close without drain).
- `h.event_log` → the list of durable entries `_enqueue_event_log` produced, so a test
  can feed them straight into `rebuild_turns()` and assert the rebuilt transcript.
- `h.crash_agent()` → SIGKILL the fakeagent process inside tmux.

The durable log is captured by stubbing the Volundr POST sink (the broker already
buffers and flushes via `_flush_event_log`); point it at an in-memory list.

## 4. Test pyramid and markers

| Tier | Marker | Runner deps | What it covers | Speed |
|---|---|---|---|---|
| Unit | (default) | none | `FakeTmuxInteractiveTransport`, reducers, parsers, broker dispatch with a stub transport | ms |
| Component | `@pytest.mark.integration` (needs tmux) | `tmux` binary + `fakeagent` | real tmux + fake agent: steering, interrupt, idle/hook turn completion, menu render+answer, custom screens via `TmuxPage` | 100s of ms |
| Broker E2E | `@pytest.mark.integration` | tmux + `fakeagent` + in-process broker/WS | `BrokerHarness`: connect → steer/interrupt/answer → crash → reconnect → assert replay + rebuilt transcript | ~1–3 s each |
| Live smoke | `@pytest.mark.e2e` (manual / nightly) | real Claude login | a thin sanity pass of the same scenarios against real Claude | minutes |

Gating follows the existing convention in `pyproject.toml` (default addopts already
exclude `integration`, `broker`, `kind_integration`). Component/E2E tmux tests skip
cleanly when `shutil.which("tmux") is None`, exactly like the current smoke. CI runs
the tmux tier on a runner that has tmux installed; the live tier is nightly/manual only.

## 5. Scenario matrix

Each scenario lists: **intent**, **tier**, **setup (fakeagent directive)**, **steps**,
**assertions**, and the **bug it guards**. IDs are stable so failures map to a row.

### A. Session lifecycle & turn completion

- **A1 start/init** (component) — start transport; assert `system/init`,
  `terminal_pane_opened`, capabilities include `terminal_*`, `steer`, `interrupt`,
  `slash_commands`. (Extends current A1-ish coverage to real tmux.)
- **A2 synthetic turn (no hooks)** (component) — `--no-hooks`, `say:hello`; assert a
  `terminal_frame` containing the text and a `result` whose `result` matches, closed by
  the idle watchdog. Guards: synthetic-turn idle detection.
- **A3 hook turn** (component) — hooks on, `say:hello`; assert turn closes on the `Stop`
  hook (not idle), `result.modelUsage` non-empty so `message_count` advances. Guards: Bug 2 usage estimate.
- **A4 long turn boundary** (component) — `work:2` with idle timeout 0.1s; assert the
  turn stays open across output and closes once, not per-frame. Guards: idle clock refresh.

### B. Steering (send while working) & restart

- **B1 idle message starts a turn** (component) — `say:` then send a message; one turn.
- **B2 mid-turn steer keeps the turn** (component) — `work:3`; send a message at t≈1s;
  assert the in-flight turn is *not* restarted (no second `_begin_turn`), the text is
  delivered (`terminal_input_sent`), and the idle clock refreshes. Guards: native steering.
- **B3 send-lock wedge is bounded** (component) — force a paste to hang; assert the
  send lock times out (`_deliver_timeout_s`) and a later steer still delivers rather than
  deadlocking. Guards: Bug 3 wedge.
- **B4 steer after reconnect** (E2E) — connect, `work:5`, drop the client, reconnect,
  steer; assert delivery ACK (`user_delivered` with matching `request_id`) reaches the
  *new* client and the agent saw the text. Guards: Bug 3 "inbound steering after reconnect".
- **B5 steer while a question is pending** (E2E) — `ask:`; with the question open, send a
  plain steer; assert defined behaviour (steer is queued/rejected deterministically, not
  silently lost, and the pending question is still answerable). Guards: lock-state race.
- **B6 ordering under burst** (E2E) — send 5 messages rapidly during `work:3`; assert
  they are delivered in order (send lock serialization) and each is ACKed.

### C. Interrupts

- **C1 interrupt cancels a turn** (component) — `work:10`; send `interrupt`; assert
  `C-c` keystroke sent and the turn finishes with `is_error`/reason `interrupted`.
- **C2 interrupt then resume** (component) — after C1, send a new message; assert a
  fresh turn starts and completes (session still usable).
- **C3 interrupt with no active turn** (component) — assert it is a safe no-op, no
  spurious `result`.

### D. Crash & reconnect

- **D1 client crash mid-turn → reconnect replays** (E2E) — `work:5`; `kill_client`
  mid-turn; reconnect; assert the new client receives the in-flight assistant state and
  the turn still completes. Guards: lost-frame on abrupt close.
- **D2 crash with question open → reconnect re-surfaces** (E2E) — `ask:`; kill client
  while the question is open; reconnect; assert `_pending_ask_user_questions` is replayed
  so the new client gets an answerable card; answer it; assert `ask_user_resolved` and
  the agent unblocks. Guards: tmux lock-state re-surface.
- **D3 agent crash mid-turn → rebuilt transcript** (E2E) — `work:2` then `crash`
  (or `h.crash_agent()`); take `h.event_log`, run `rebuild_turns()`; assert the partial
  assistant turn is present and flagged `metadata.status == "interrupted"`, with
  deterministic `uuid5` ids stable across two rebuilds. Guards: Bug 2.
- **D4 rebuild is fallback-only & no double count** (unit) — feed a log that has both
  `conversation.turn` rows and overlapping raw frames (same `request_id`); assert the
  authoritative rows win and raw frames are not double-counted. Guards: Bug 2 invariants.
- **D5 reconnect does not duplicate a settled question** (E2E) — answer a question, then
  reconnect; assert the answered question is *not* replayed. Guards: ask-resolved vs result race.
- **D6 conversation endpoint fallback** (integration, volundr) — with live/archive empty
  but a durable log present, `GET /conversation` returns the rebuilt turns. Guards: Gap A path.

### E. `ask_user_question` in tmux (the reported breakage)

- **E1 question surfaces to frontend** (component/E2E) — `ask:Database|Which?|Postgres;SQLite`;
  assert a structured `ask_user_question` frame is broadcast with `request_id`, `questions`,
  options, and that the session flips to `awaiting_input`. Guards: "message not sent to frontend".
- **E2 answer translates to the right digit** (component) — answer "SQLite"; assert
  `_match_menu_digit` picks row 2, `send-keys 2 Enter`, the agent receives the choice, and
  `ask_user_resolved` is emitted carrying the chosen label.
- **E3 menu-not-yet-rendered race** (component) — make `fakeagent` render the menu ~400ms
  *after* the question event; answer immediately; assert the bridge *waits for the menu to
  render* (polls capture) before sending the digit, rather than sending into the prompt.
  Guards: the reported keystroke-lands-wrong race. (If current code fails this, it is a
  found bug — fix is to gate the keystroke on a successful `_capture_menu_rows` match.)
- **E4 multiselect** (component) — options with `multiSelect`; assert each selection +
  submit maps to the correct keystroke sequence.
- **E5 deny path** (component) — choose Deny; assert `Escape` is sent (not a digit).
- **E6 SDK ↔ tmux parity** (component) — the *same* logical question via SDK transport and
  via tmux produces the same structured frame shape and same `ask_user_resolved` contract.
  Guards: cross-mode drift (the frontend should not care which transport it is).
- **E7 turn end clears a stale prompt** (component) — leave a question unanswered, let the
  turn end; assert pending prompts are cleared and not replayed later.

### F. Permission modes (not running all-permissions)

- **F1 default mode surfaces a gate** (E2E) — start with `permission_mode="default"`,
  `perm:Bash|rm -rf build`; assert a structured permission request reaches the client
  (as `claude_permission_request` + `ask_user_question`), session is `awaiting_input`.
- **F2 allow / allow-and-don't-ask / deny** (E2E) — each choice: assert the right keystroke
  and that `permission_resolved` is broadcast; deny → tool not executed.
- **F3 plan mode** (component) — `permission_mode="plan"`; assert edit/execute tools are
  gated and a plan is presented; assert `set_permission_mode` switches it live.
- **F4 acceptEdits** (component) — edits auto-approve, shell still gates. Assert the matrix.
- **F5 bypass vs ask conflict** (unit) — `skip_permissions=True` + `ask_user_question_enabled=True`:
  assert the documented precedence (bypass wins / questions never surface) and that the
  config is validated rather than silently contradictory. Guards: confusing flag combo noted in code.
- **F6 auto-approval policy** (integration) — wire the Volundr policy engine; a gate the
  policy allows is auto-resolved after `delay_seconds`; a gate it doesn't stays for the human.
  Reuses `tests/test_volundr/test_permission_auto_approval.py` machinery end-to-end.

### G. Custom tmux surfaces (forward-looking, via `fakeagent` `screen:` + `TmuxPage`)

These features are partly aspirational in the product; the tests are written against
`fakeagent` screen fixtures so the **navigation/assertion harness** is proven now and
the same tests point at the real screens as they land. Each asserts: reach the screen,
read its structured content, act on it, observe the effect.

- **G1 agent selection** — render the agent-picker screen; `TmuxPage.menu_rows()` lists the
  agents; select one; assert the selection is reflected (the chosen agent label appears /
  an event is emitted). Maps to `--teammate-mode tmux` / `/agents`.
- **G2 see running agents** — render a running-agents table (panes or a list view); assert
  the harness enumerates them (`panes()` for pane-per-agent, or parsed rows for a list) and
  their status. Ties to `terminal_pane_opened/closed` events and the Ravn
  `/api/v1/ravn/sessions` view.
- **G3 workflow view** — render the workflow board; assert the harness reads workflow
  name/status and can trigger one (slash command or key), observing a state change.
- **G4 teams of agents** — multi-pane/window team layout; assert navigation between panes
  (`select_pane`/`select_window`), that input routes to the *targeted* agent, and that each
  pane's output is independently captured. This is also the multi-agent steering target-routing
  test (extends B-series to "which agent did the steer reach").
- **G5 navigation invariants** — golden-snapshot the key screens (`page.snapshot()`),
  assert chrome filtering (`_extract_assistant_response`) so assistant text is cleanly
  separable from menus/spinners on every page.

## 6. Cross-mode parity matrix

A frontend should behave identically regardless of transport. Run a parametrized parity
suite over `{sdk, persistent_subprocess, tmux_interactive}` (and `codex_ws` where
applicable) asserting the *contract*, not the internals:

| Contract | sdk | persistent | tmux |
|---|---|---|---|
| `capabilities` advertise what the mode supports | ✓ | ✓ | ✓ |
| message → `result` with usage | ✓ | ✓ | ✓ |
| interrupt cancels, session reusable | ✓ | ✓ | ✓ |
| steer mid-turn (where `caps.steer`) | ✓ | n/a | ✓ |
| `ask_user_question` frame shape + `ask_user_resolved` | ✓ | ✓ | ✓ |
| permission request frame shape + `permission_resolved` | ✓ | ✓ | ✓ |
| durable log → `rebuild_turns()` reproduces the turn | ✓ | ✓ | ✓ |

Implementation: a single `@pytest.mark.parametrize` over a transport-factory fixture, so
each new contract test automatically covers every mode and surfaces drift (e.g. the
reported tmux `ask_user_question` divergence) as a failing cell.

## 7. Live (real Claude) smoke

Keep a small, manually/nightly-run mirror (`@pytest.mark.e2e`) of A1–A3, B2/B4, C1,
D2, E1–E2, F1–F2 against real Claude (subscription auth), reusing `TmuxPage` and
`BrokerHarness` but pointing `PATH` at the real `claude`. Prereqs match
`skuld-claude-interactive.md` (tmux installed, subscription login, `SKULD__CLAUDE_AUTH`
unset/`subscription`). This catches drift between `fakeagent` and the real CLI's TTY
chrome / menu format — when the real CLI changes its menu rendering, E2/E3 here fail
first and tell us to update the parser + `fakeagent` screens together.

## 8. Phasing

1. **Phase 0 — harness** (unblocks everything): build `fakeagent` (REPL + directives +
   hooks), `TmuxPage`, `BrokerHarness`. Port the existing bash-echo smoke to `fakeagent`
   to prove parity. Deliverable: green component test A2/A3 on real tmux.
2. **Phase 1 — steering, interrupt, crash/reconnect** (B, C, D): the highest-value bug
   classes the user named first. D3/D4 reuse the existing reducer tests.
3. **Phase 2 — questions & permissions** (E, F): fixes the reported tmux
   `ask_user_question` breakage; E3 likely surfaces a real bug to fix.
4. **Phase 3 — parity matrix** (§6): lock the cross-mode contract so future modes inherit
   coverage.
5. **Phase 4 — custom surfaces** (G): land the navigation harness against `fakeagent`
   screens; re-point at real screens (agent selection, running agents, workflows, teams)
   as those features mature.

Phases 1–2 are the ones that pay down the current pain; Phase 0 is the prerequisite and
should be a single focused PR.

## 9. Definition of done

- `fakeagent`, `TmuxPage`, `BrokerHarness` live under `tests/support/forge/` with their
  own unit tests (the harness must itself be trustworthy).
- Component + E2E tmux tiers run in CI on a tmux-enabled runner, skip cleanly without tmux,
  and stay credential-free.
- Every scenario ID in §5 maps to a named test; the bug-guarding rows (B3, B4, B5, D1–D5,
  E1–E3) each reference their originating commit in a docstring.
- Coverage stays ≥ 85% (project gate) and no new pytest warnings.
- The parity matrix (§6) is green for all three primary modes, with any drift either fixed
  or explicitly `xfail`-documented as a known mode limitation.

## Outcomes (implementation)

All five phases shipped on `lexi/cli-tmux-questions`. The harness lives in
`tests/support/forge/` (`fakeagent.py`, `tmux_page.py`, `hook_server.py`,
`broker_harness.py`, `multipane.py`, `fakeclaude_shim.py`, `screens/`); the suites are
`tests/test_skuld/test_forge_*.py`. Run the tmux tier with
`SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration tests/test_skuld/test_forge_*.py -rs`.

Final state: forge integration tier **44 passed, 2 xfailed** (E4 multiselect bridge
limitation; D6 needs Postgres); full `tests/test_skuld` default tier **1454 passed, 0
failures**.

Real product bugs found and fixed (each guarded by a named scenario):

1. **interrupt→resume watchdog wedge** (`tmux_interactive.py` `_begin_turn`) — a
   resumed turn after an interrupt never closed because the new `_turn_done` got no
   watchdog. Guards: C2.
2. **`awaiting_input` clobber** (`broker.py` `_handle_cli_event`) — an AskUserQuestion
   gate's tool_use frame scheduled an `active` report that overwrote the question's
   `awaiting_input`, so a blocked session looked active. Guards: E1b.
3. **menu-render race** (`tmux_interactive.py` `_answer_tty_prompt`) — an answer
   arriving before the on-screen menu rendered pressed the default "1"; now bound-polls
   the live menu (`SKULD__TMUX_MENU_RENDER_WAIT_SECONDS`). Guards: E3.
4. **`_match_menu_digit` precedence** — "Allow & don't ask again" matched row 1 "Allow"
   by substring and silently downgraded a persistent allow; now exact-pass first.
   Guards: F2.

Consistency fixes surfaced along the way: a hermetic `tests/test_skuld/conftest.py`
(this dev box leaks a live Forge session's `SKULD__*` env into `SkuldSettings`, which
was breaking ~11 broker tests locally), realigning a stale broker dispatch test broken
by an earlier commit, and de-flaking `test_real_tmux_smoke_with_fake_claude`.

Open follow-ups (documented xfail/TODO, not regressions): tmux multi-select keystroke
bridge (E4); a credentialed live tier for plan-mode edits-gating (F3) and the D6
conversation-endpoint fallback; and re-pointing the G1/G3/G4 surface tests at the real
`/agents`, `/workflows`, and teams screens as those product surfaces land.
