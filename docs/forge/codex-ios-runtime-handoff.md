# Codex runtime alignment: native-client handoff

September 13, 2026. **Documentation only; no iOS changes, build, release reservation
or physical-device acceptance performed by this runner.** A separate session owns
native implementation. The server candidate includes the fixed Project UX input
`11e9e0c668747fd0af1b97463f2c238e9fb40811`; do not import a moving UX branch.

## September 14 steering/reconciliation follow-up

The later Spark user test exposed a separate chronological replay and transient
native duplicate-display problem. See the [bounded native handoff](codex-steering-timeline-ios-handoff-20260914.md).
The September 13 control compatibility below is **not** proof that the existing
iOS history reconciliation supports the new item-based display fragments.

## Core fixes require no new client control

An API-only release may close the proxy WebSocket while the gateway and native
turn remain alive. Reconnect to the **same Forge session**, recover history and
the live tail using the existing cursor/identity contract, and do not resend the
last prompt merely because the connection closed. The isolated release proof
exercises real HTTP/WebSocket reconnect and output generated during the API gap;
it is not physical-device or rendered-iOS proof. A separate native owner should
verify that existing reconnect behavior when performing client acceptance.

Existing live gateways intentionally retain their loaded source during API
cutover. Do not assume the API's version proves the session has the new Codex
adapter. Use a new session or an owner-approved saved-boundary gateway transition
for acceptance of the new transport behavior; this runner does not restart user
sessions or reserve a native release.

The existing assistant/content-block/tool-result envelopes remain compatible.
Server normalization now preserves public commentary and final text around tools,
identifies every streaming block, reports native tool failures accurately, and
routes busy Codex input as live steering rather than interrupt/replacement.
Clients should already fold item identity and index rather than “last open block.”

Native QA should compare raw public items, server durable replay, channel output,
and rendered parts. Keep commentary and final-answer items separate and ordered;
upsert authoritative completion by item ID rather than appending it twice. Tool
input updates with the same call ID are replacements, not another JSON delta.
A completion-only tool event must never close an unrelated public text block.
Retain HTTP detail recovery for large frames and do not truncate stored history.

The source regression/native evidence proves server paths, not that a particular
iOS screen is correct. Obtain the user's affected session/client version if a
rendering discrepancy remains. Do not hide real user steering as an internal
agent update, or infer authorship from role/text.

## Optional model / effort / speed UI

Check `GET /s/{id}/api/capabilities`: `runtime_options: true` gates the new surface.

- `GET /s/{id}/api/runtime-options?refresh=true` reads the session's **native**
  paginated model catalog. Fields: `models`, `current`, `active_turn`, `source`,
  `applies_to`. Each model has `model`, display `name`, `effort_levels`,
  `default_effort`, `service_tiers`, `default_service_tier`, `input_modalities`.
  Do not assume these equal Forge's launch catalog or encode them in Swift.
- `POST /s/{id}/api/runtime-options` accepts
  `{"request_id":"client-id","options":{"model":"gpt-6-astra","effort":"xhigh","service_tier":null}}`.
  Options are sparse; explicit null clears the tier, omission retains it.
  A selection is validated together against native availability and acknowledged
  through `thread/settings/update`. No silent effort clamping or paid-speed choice.
- WebSocket controls: `get_runtime_options` (optional `refresh`) and
  `set_runtime_options` (`options`, `request_id`). Reply type `runtime_options`
  carries the same state and correlation. Existing `set_model` uses the same
  validated path; existing `set_effort` and `/effort` remain supported.
- `current` is the acknowledged **next-turn** choice. `active_turn` retains the
  executing model/turn, including native reroutes. A success does not claim an
  in-flight model changed or the user's task finished. Failed/uncertain controls
  must not be blindly retried. HTTP 400 invalid selection, 409 unsupported harness,
  503 uninitialized transport, 502 native failure/disconnection.
- Choices persist through broker restart unless the operator changes the launch
  model. Native account availability can change: refresh and display rejection,
  never substitute another model or permission policy.
- Claude sessions (tmux transport) expose the same shape with `source: "catalog"`.
  A typed `/model <id>` or `/effort <level>` saves the choice as the default for
  every new Claude Code session on the host (`~/.claude/settings.json`), so the
  broker never types them (LEX-2022). It opens the `/model` picker or the `/effort`
  slider, moves to the choice and presses `s` ("this session only"), then waits for
  the CLI's "for this session only" acknowledgement; a "saved as your default"
  acknowledgement is an error. The picker lists only its aliases' current models,
  so `models` are `SESSION_SWITCH_MODELS` in `skuld.transports.tmux_interactive`
  (Opus 5.5 and Fable 5.2; `name` is the model id, `default` marks the launch
  model, no service tiers). Any other model is refused with a 400 that says to set
  the session's model (`PUT /sessions/{id}`) and relaunch. `current.model` follows
  the native transcript, so a `/model` typed in the terminal shows after the next
  reply. Changes happen between turns only (400 while Claude is working). Not
  offered behind a model gateway (`runtime_options: false`).
- An applied switch (a `runtime_options` frame with `applied: true`, or an applied
  `effort_status`) is recorded on the session row: `model` and
  `workload_config.reasoningEffort`, which the next launch uses. `PUT
  /api/v1/forge/sessions/{id}` takes `model` and `reasoning_effort` (checked against
  the model's levels) for the same record.
- The session's `model` in `GET /api/v1/forge/sessions`, the session GET and the
  `session_updated` event is the model **in effect**: the last one the runtime
  reported (a reply's `message.model`, or an acknowledged change between turns),
  else the launch model. The launch model stays stored, updated by an applied
  switch; restart launches with it. Sessions show it as `launch_model`, with
  `launch_effort` (`workload_config.reasoningEffort`, null at the model's default),
  so a `PUT` is visible before the CLI reports anything new.
- Per-session launch environment (LEX-2045): `PUT /api/v1/forge/sessions/{id}` takes
  `launch_env`, a `{name: value}` map stored whole as `workload_config.launch_env`
  (`{}` clears it), and session create accepts the same key in `workload_config`.
  Every start and restart gives it to the broker, whose environment the CLI inherits,
  so a lane can launch with, for example, `CLAUDE_CODE_SUBAGENT_MODEL=opus` and
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW=150000`. Only names in Volundr's
  `session_launch_env` are accepted, each with a rule: a whole-value `pattern`, or an
  integer `min_int`/`max_int` (defaults: those two variables; the window is 100000 to
  1000000). Anything else is a 400 naming the allowlist, and a stored map the
  allowlist no longer admits stops the start with the same error. Sessions show it as
  `launch_env`. Like `launch_effort`, it does not change a running CLI.
- With history, Claude asks "Switch model?" / "Change effort level?" after `s`.
  The broker reads the dialog from the live screen (border or not) and presses
  Enter only on "Yes, switch to <choice>", once the dialog has stayed unchanged
  for `effort_confirm_settle_s` (1 s; chart `broker.effortConfirmSettleSeconds`).
  An Enter sent as the dialog first draws can be lost, and the switch then timed
  out and was cancelled ("Kept model as …"). A dialog still there, unchanged, a
  settle period later gets Enter again.

## Approvals, questions and notices

Existing `control_request` retains exact command/native context. One-time/session
approval and deny/cancel are distinct. Historical `allowForever` means the native
**session**, not a permanent policy rule. Respect native `availableDecisions`.
Unsupported input/policy edits are rejected rather than silently discarded.

MCP elicitation has `auto_approval_allowed: false`; it cannot be auto-submitted by
sandbox permission settings. An explicit WebSocket `permission_response` may
include structured `content`. A form requires a real schema-aware editor; clients
without one can decline/cancel, never fabricate a completed form. Treat URL
elicitation as explicit user consent and don't automatically visit arbitrary URLs.

`control_resolved` clears the exact pending native control; do not invent which
choice was made. Surviving stale controls may be non-answerable after process loss.
Keep question/approval request identities and existing recovery errors.

System `runtime_notice` is nonterminal (for example a retry or model reroute),
not an assistant answer and not a failure of the entire turn. Never show private
reasoning or raw credentials/config data as a notice.

## Explicitly out of this native handoff

Slash-menu redesign is [future work](codex-future-command-capabilities.md).
No native realtime replacement for Lexi Voice/Live; no new UI release is implied.
Media/skill/mention composition, artifact presentation, richer permissions and
session/worktree operations require separate agreed client/server contracts, not
string commands that pretend the workflow is complete.
