# Codex runtime alignment: native-client handoff

September 13, 2026. **Documentation only; no iOS changes, build, release reservation
or physical-device acceptance performed by this runner.** A separate session owns
native implementation. The server candidate includes the fixed Project UX input
`11e9e0c668747fd0af1b97463f2c238e9fb40811`; do not import a moving UX branch.

## Core fixes require no new client control

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
