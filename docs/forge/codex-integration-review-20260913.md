# Codex / Skuld integration review — September 13, 2026

## Status and scope

This is an **in-progress engineering sweep**, not a declaration of complete Codex
feature parity and not a deployment. It covers the owned Codex app-server adapter,
Forge controls, channel projection, history/replay, and adjacent harness boundaries.
Native iOS UI and project instruction/provenance behavior remain separately owned.

- Runner: `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`.
- Checkout: `/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913`.
- Starting source: `c6d80980c304f972461d9e31d36ac3aeca35cc8d`.
- Coordinator scope confirmation: `c5d7dd4f-50f2-4ea5-b801-7db65574ad48`.
- Fixed Project UX integration input: `11e9e0c668747fd0af1b97463f2c238e9fb40811`.
  Its live-steering fix is `a5acb7c02a3a5d6fc2be9732e0b11fe28660a87d`.
- No shared runtime restart, deployment, production configuration edit, new Forge
  session, or iOS release is part of this work.

## Current upstream evidence

The installed binary reports **codex-cli 0.154.0**, matching the latest ordinary
CLI release found in the official changelog, dated September 9. September 10 also
has a matching Python SDK release. Relevant changes include Astra selection,
inline asynchronous questions, experimental worktrees, plugin refresh, saved
permission fidelity on resume/fork, and approval behavior across steering and
compaction. This does not imply every desktop feature is an app-server endpoint.
[Official changelog](https://learn.chatgpt.com/docs/changelog).

Offline schema generation from this exact binary produced 99 stable client
methods, 159 with experimental APIs, 10/11 server-request methods, and 81 server
notifications. The generated ThreadItem union has 19 variants. These counts come
from local schemas, not a claim that Forge implements those methods.

The [complete method inventory](codex-protocol-inventory-0.154.0.json) records
every generated method and whether its literal name occurred in the starting
adapter. **A literal match is not implementation or acceptance evidence.** In
particular, ignored notifications and internal calls are not public capabilities.
The generated schema also marks some methods stable where explanatory web text
still labels them experimental; the pinned binary contract must decide testing.

The official contract distinguishes live input from cancellation and native
items from their incremental deltas. Dynamic tool execution is requested through
`item/tool/call`; a raw history/notification item is not that request. Slash
commands are client affordances over specific operations, not a generic remote
TUI parser. [App-server documentation](https://learn.chatgpt.com/docs/app-server).

## Confirmed baseline defects and isolated corrections

| Area | Finding | Correction / evidence |
| --- | --- | --- |
| Public text between tools | `channels.filter_internal_blocks` used one last-open block type. A tool starting while text was open suppressed subsequent public deltas and the text stop. Large, already-streamed messages can intentionally omit their duplicate completion, making this more than a transient display problem. | Scoped block identity tracking, typed-delta handling, visibility-toggle continuity, and reset at result. Adapter → default channel → shared reducer tests reproduce short and large losses before the fix. |
| Live steering | Broker normal delivery selected `redirect` for all busy steerable transports, even when they advertised live input. Codex redirect deliberately interrupted and replaced the turn. | **Do not duplicate:** compose the coordinator-pinned UX fix, preserving exact message/request identities, explicit Stop, ambiguity, and no-resend semantics. |
| Model selection | `set_model` changed local state but `turn/start` omitted the selected model. | Include the model on the native turn request; regression checks the wire params rather than the local variable. |
| Public plans | Native `plan` items and `item/plan/delta` were ignored. | Use the existing item-identity text lifecycle and authoritative completion reconciliation. |
| Startup substitution | A fresh app-server startup error activated an exec adapter with different capabilities and default permissions. | Remove automatic adapter substitution; clean up and fail explicitly. Explicitly configured legacy exec remains a separate adapter. |
| Unknown server requests | Unknown methods received an invented approval response. | Reply with JSON-RPC method-not-found, retaining the original request ID. |
| Unsent approvals | A missing socket returned normally from response sending, allowing pending approval state to be discarded. | Raise on the missing socket and retain the pending request. |
| Raw tool ownership | Raw `shell_command` notifications and rollout function-call frames entered a second execution/output-injection path. | Normalize raw calls and native outputs observationally. Keep client execution on the explicit dynamic-tool server-request path. Both old paths fail new offline regressions. |
| Slash-command correctness | Unknown commands were ignored; RPC failures became a notice followed by apparent success; arguments could be discarded. | Propagate errors into existing WebSocket control errors and HTTP 400/502 responses. Never automatically retry an uncertain command. |
| Command semantics | Goal control words became replacement objectives; `/title` conflicted with current native TUI meaning; broker normalization discarded method/hint fields. | Real goal pause/resume/clear/edit operations, canonical `/rename`, explicit legacy alias, preserved capability/method/hint metadata. |
| Approval wire variants | Modern session approvals were reduced to one-time approval; legacy patch/denial responses used incompatible enums; Telegram's `allowOnce` was declined. | Method-specific response mapping preserves one-time/session/deny/cancel semantics and checks native `availableDecisions` when present. Missing/unknown choices fail explicitly. |
| Permission profiles | Permission requests were displayed as file edits and answered with a command decision, not granted permissions. | Preserve the exact requested profile and grant it only on explicit allow, scoped to turn/session; deny grants nothing. Do not accept client-supplied wider grants or silently discard input/policy edits. |
| MCP elicitation | Disabling sandbox approvals could auto-submit an invented empty form; the broker discarded explicit form content. | Always require human consent/input, preserve structured content through the existing WebSocket control route, and never let generic permission auto-approval answer an elicitation. Native MCP validates its requested schema. |
| Native control cleanup | `serverRequest/resolved` was ignored, leaving canceled or externally answered cards pending on reconnect. | Retire exact opaque RPC identities without inventing the decision, clear broker attention/persisted controls, and retain resolution frames for replay. Socket-reader cleanup never waits behind an answer awaiting that same reader. |
| Resume identity / history parameters | Public resume could replace the requested conversation with a different returned ID or invent success from an empty response. Removed `persistExtendedHistory` flags were still sent. | Require an identified, matching resumed conversation; remove obsolete flags. Keep the upstream default legacy history contract, not unsupported paginated creation/resume. |

Source anchors: `src/skuld/transports/codex_ws.py`, `src/skuld/channels.py`,
`src/skuld/broker.py`, `src/skuld/broker_api.py`. Regression evidence is in
`test_codex_protocol_fidelity.py`, `test_codex_text_identity.py`, and existing
channel/transport/broker tests.

## Slash-command review and API contract

The [command inventory](codex-command-inventory-20260913.json) accounts for
54 distinct names: the official CLI table including aliases, plus `/worktree`
from the release notes. It classifies native actions, missing Forge mappings,
compatibility conflicts, and client/platform-specific behavior. It does not copy
the TUI menu into an executable server allowlist.
[Official command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

The candidate advertises nine adapter commands: `/compact`, `/review`, `/goal`,
`/title` (legacy alias), `/fork`, `/rename`, `/status`, `/skills`, and `/mcp`.
The generic broker separately provides `/effort`. Skills are discovered through
Codex for the current workspace; MCP listing consumes native pagination. These
are real, bounded mappings, not claims of full TUI-menu equivalence.

Existing authenticated session routing exposes:

- `GET /s/{session_id}/api/slash-commands` for the runnable catalog.
- `POST /s/{session_id}/api/slash-commands/send` with `command` and `arguments`.
- The session WebSocket's `slash_command` and `discover_slash_commands` controls.

HTTP `sent` retains its existing compatibility meaning: native dispatch returned
success, **not** that review/compaction or the user's objective finished. An RPC
timeout can be ambiguous; callers must not blindly retry stateful actions.
Ordinary `/sessions/{id}/messages` accepts user text and is not a general slash
command endpoint. `/skills` and `/mcp` currently list resources; they do not
pretend to implement native interactive pickers or skill invocation arguments.

Still required for complete command UX: typed control results, durable command
IDs/outcomes for safe reconnect/retry, dynamic model/effort/tier selection, real
mode/permission selectors, client-only affordances, and a Forge-managed result
for native forks. Unimplemented entries must not be advertised as runnable.
Deleting, logging out, sharing diagnostics, installing plugins, or stopping
background terminals must retain their distinct consent and lifecycle semantics.

## Capability sweep: remaining implementation opportunities

| Family | Current assessment / next work |
| --- | --- |
| Model/effort/tier catalogs | Wire fidelity corrected; availability/effort still relies partly on static model tables. Use native `model/list` and provider capabilities, distinguish requested next-turn settings from effective runtime state, and handle reroutes. |
| Live input / queues | Pinned live-steering correction covers normal input. Native queue CRUD/reorder/start, client user-message IDs, mid-turn setting updates and explicit next-turn scheduling require separate contracts. |
| Thread history | Forge has native import, durable event logs and a shared reducer. Removed obsolete persistence flags and made resume identity fail closed. Upstream documents legacy as the default; paginated creation, full-history hydration and resume are not yet supported there. A schema method name alone does not make this usable. Large-history import/hydration and thread-status acceptance remain to be exercised on owned native sessions. |
| Approvals / questions | Basic modern/legacy decisions, requested permission profiles and native cancellation are corrected. Partial-profile grants, policy amendments and strict-review controls still need explicit UI/API contracts; arbitrary rule/input edits are rejected, not silently applied. User-question timeout/async semantics need additional native acceptance. |
| MCP / plugins / hooks / apps | Read-only skill/MCP commands and explicit elicitation response transport are implemented. Native form/URL UI, schema-aware form rendering, refresh/invalidation, OAuth challenges, plugin consent, hooks and connected-app inputs remain incomplete. No automatic URL visit or fabricated form submission is performed. |
| Streaming / tool output | Public text/plan corrections are covered offline. Review plan/diff updates, MCP progress, terminal interactions, patch updates, artifact/image items and async tool output without changing native execution ownership. |
| Recovery / lifecycle | Startup now fails closed and unsent approvals remain pending. Native event continuity, stale callbacks, socket-loss ambiguity, competing writers and process recovery still require authorized owned-provider proof. |
| Session operations | Archive/unarchive/delete, rollback/revert, fork ancestry, side chats, worktrees and background terminal lifecycles must agree with Forge persistence and authorization; do not expose bare native mutations as complete Forge workflows. |
| Inputs / outputs | Normal message delivery is text-centric. Native image/audio/skill/mention inputs, schema-constrained outputs and user-facing artifacts need typed, harness-neutral envelopes and replay parity. Preserve existing Lexi Voice/Live rather than replacing it with experimental native realtime. |
| Diagnostics / account | Native warnings, config notices, model/auth recovery and usage/rate-limit notifications deserve explicit nonterminal presentation. Never dump credential/config material into ordinary chat. |

## Tmux and harness boundaries

`TmuxInteractiveTransport` is explicitly a **Claude Code interactive adapter**;
its hook names, slash discovery and TTY behavior are not a Codex protocol layer.
`CodexWebSocketTransport` is the primary structured Codex path. The legacy exec
adapter and managed remote-control adapter have different semantics; substituting
one on failure is not resilience. A future Codex TUI attachment must use its real
native server/terminal ownership contract, not relabel Claude hooks or scrape
terminal repaint bytes into an authoritative database transcript.

Project workflow/instruction ownership stays with the pinned UX change. No new
coordinator-specific server engine or model-specific workflow policy is introduced.

## Evidence and limitations

Scratch evidence lives in `.local/codex-review-20260913/` in this checkout:
schema bundles, official source snapshots, failing-before/passing-after logs,
scope receipts and a read-only comparison of this runner's own native/Forge text.
All four public assistant items present at that snapshot matched decoded Forge
conversation text. That small active-session sample neither reproduces nor
disproves the user's display report. A specific affected session/client remains
valuable for native → stored → public wire → rendered comparison.

**Test-isolation incident:** an initial startup regression mocked the fallback's
start but not its send method, unintentionally launching three short real Codex
exec turns in pytest temporary directories. They completed before targeted
termination; matched rollouts contain no function/custom-tool-call records.
There were provider token records. The user and coordinator were informed;
receipt `34ec53bf-76dd-482c-bb03-a50ecb98a54f` records the correction. That run is
excluded from offline and live-acceptance evidence. Both methods are now mocked
and the new protocol-test module forbids real subprocess launches. Do not claim
this entire review involved zero provider calls.

**Credential hygiene:** the checkout's existing Git remote contained embedded
authentication material and a remote-inspection command exposed it in this
session's tool output. It is not copied into these documents or receipts.
The user was notified; separate credential rotation is recommended. Shared Git
configuration and authentication were not changed by this runner.

## Combined-source checkpoint

The fixed UX pin was merged into the committed runtime changes at
`087050587fa9efb7c3c58d2bc5f2fe71594efa4d`, with no textual merge conflicts.
Review confirmed that live input still selects `steer`, exact accepted turn IDs
are checked, and the new model/plan/error/raw-observation fixes remain present.
One inherited steering test's exact `turn/start` expectation needed the newly
required model field; that test-only reconciliation does not change steering.

Initial broad collection with two module-named coverage targets hit a Pydantic
lazy-import failure. Using the source directory as the coverage target avoids
that collection problem. The coverage report is explicitly scoped to the two
substantially changed transport/channel modules and retains the 85% gate; it is
not a claim of repository-wide coverage. No dependency, coverage threshold, or
production configuration was changed to obtain a passing result.

First-checkpoint combined sweep on Python **3.13.13** / pytest **9.1.1**:

- **2,549 passed, 22 skipped, 82 deselected, 1 expected failure**, no test failures
  or reported warnings; elapsed 72.13 seconds. Skips/expected failures are not
  counted as passes.
- All `tests/test_skuld` and `tests/test_projects`, plus shared transcript-reducer
  parity, database-conversation fallback, PostgreSQL history-import adapter and
  external-history service tests. Real provider/integration/broker/kind tests
  were excluded by marker. Database adapter checks use test infrastructure, not
  production database queries.
- Transport/channel statement-and-branch coverage **90.04%**, exceeding the
  unchanged 85% scoped gate. This is not whole-repository coverage.
- Ruff check/format and `git diff --check` passed for changed Python files.
- Detailed, sanitized counts, source pins and log digests:
  [validation evidence](codex-review-evidence-20260913.json).

Validation must distinguish unit/fixture replay from native provider execution
and physical-client proof. Complete integration, authorization of an owned live
matrix, and rollout acceptance are separate gates. The mission remains open.

## Approval and history-contract follow-up

Runtime follow-ups are committed at `3cfa0e9c` (approval/control resolution) and
`a75ba536` (history parameters/native resume identity), on the same fixed UX pin.
The first follow-up produced 21 failing-before offline regressions, then 619
passing targeted transport/broker/control-replay tests including the reader-lock
edge case. Seven history/identity regressions failed before correction; the
transport/identity/steering/project-context set then passed 331 tests, with four
excluded by marker. Existing tests that invented a new ID on resume were corrected
to return the requested native ID; a separate mismatch regression requires failure.

Independently, **35 actual response variants** from the candidate were validated
with `Draft7Validator` against the exact installed 0.154.0 generated response
schemas. This covers modern command/file approvals, legacy exec/patch decisions,
permission grants, and MCP form/URL actions. It is wire-shape validation, not proof
that an external action ran or a native permission policy was changed.

Native command/network/stdin/approval context now survives the control-request
projection. A form is still a generic `control_request` for client compatibility;
its `auto_approval_allowed: false` is enforced by the broker. Manual WebSocket
`permission_response` accepts optional structured `content`; accepting a form
without explicit content fails while retaining the pending request. Clients without
a form editor can decline/cancel, but cannot pretend to submit a completed form.
Legacy `allowForever` maps to the native **session** scope, not a promise of a
persistent cross-session policy amendment.

The launch environment prepends another checkout to `PYTHONPATH`. Pytest's existing
`pythonpath = ["src"]` selected this checkout in earlier tests; the final follow-up
sweep explicitly pins `PYTHONPATH` to the owned `src` and records loaded module
paths. An initial standalone schema-validation import hit the other checkout and
failed before validation; only the corrected owned-source run is counted above.

The **final follow-up combined sweep** passed **2,595 tests**, with **22 skipped,
82 deselected and one expected failure**, no failures or reported warnings, in
72.99 seconds. Coverage scope was **expanded** to include all four production
files edited by this runner: Codex transport, channels, broker and broker API.
Aggregate statement/branch coverage is **86.66%**, above the unchanged 85% gate.
Individual file values are recorded rather than implying every file exceeds 85%:
90.09% transport, 90.82% channels, 84.37% broker and 79.43% broker API. This remains
a scoped backend result, not repository-wide, frontend, or live-provider coverage.
[Follow-up evidence and source-path audit](codex-review-followup-evidence-20260913.json).

Next acceptance gates remain: a user-identified missing-message session/client;
authorized native steering/approval/cancellation/socket-loss/recovery cases; native
to database to public-wire to rendered transcript comparison; and separately owned
native command/form UX. This is an isolated candidate, not a rollout instruction.
