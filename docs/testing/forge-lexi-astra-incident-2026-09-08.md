# Lexi iOS Astra: lost capture and native conversation replacement

The September 8 incident is a confirmed Forge/Skuld integration failure. A valid
Codex free-text question crashed the event receiver. Codex kept executing the
original request, while a later iOS connection caused Skuld to create a different
native conversation behind the same Forge session. The displayed history and
the context supplied to the model then described different conversations.

## Scope and evidence

- Forge session: `00e770e8-4948-4fb1-8952-8eea80ef50f3`, `lexi-ios-astra`.
- Model: `gpt-6-astra`; native turn contexts record `xhigh` effort.
- Original Codex thread: `01a07ff5-428f-7ac2-bf81-fa1d72503524`.
- Replacement Codex thread: `01a07ff8-ba4a-79d0-b399-77b7791c93d0`.
- Broker PID during the incident: `1827006`; there was no broker or platform
  restart between those two native thread initializations.
- Collected the complete public event log, direct read-only PostgreSQL rows,
  current conversation projection, both native Codex JSONL traces, and the
  broker/platform logs around the failure. Raw evidence is stored locally under
  `/tmp/forge-lexi-astra-investigation/`, rather than checking workspace tool
  outputs and private conversation history into the repository.
- Snapshot: 460 database rows, 428 public wire rows. Sequences are strictly
  increasing with no gap/conflict sentinels; the public wire projection matches
  PostgreSQL exactly. This proves persistence of what the broker received, not
  completeness against native execution after its receiver died.

## Observed sequence (UTC)

| Time | Evidence | Consequence |
|---|---|---|
| 07:39:35 | Database seq 1 initializes the original native thread | One Forge session starts normally |
| 07:40:19 | Seq 8–13 records and acknowledges “Effort to xhigh”; original native trace contains it | First prompt reaches Codex |
| 07:41:47 | Seq 39–44 records, delivers, and consumes the full four-part iOS review request; native original thread contains the same text | Review starts with the correct request |
| 07:43:04.243 | Native asynchronous question has `questions[0].options: null` | A supported free-text question reaches the adapter |
| 07:43:04.248 | Broker traceback: `_handle_item_completed` iterates the null options value | Receiver exits with `TypeError: 'NoneType' object is not iterable` |
| 07:43:04.248 | Seq 282 emits an error result labeled `Codex WebSocket closed` | Forge reports terminal/idle state despite ongoing native work |
| 07:43:21.661 | iOS reconnect causes `handle_websocket: transport not alive, starting...` | The same broker starts another app-server |
| 07:43:22.282 | Seq 284 initializes the replacement native thread | Old UI history remains, but subsequent prompts target an empty native conversation |
| 07:50:55 | Seq 293–298 delivers “Still going?” into the replacement thread | Codex accurately says the earlier task is absent |
| 08:04:43.965 | Original native trace records successful review completion | The final answer never enters this Forge session's captured stream |
| 08:35:52 | Seq 400–405 delivers the next context question into the replacement thread | It can recall only the follow-ups |

All four recorded human prompts have exact counterparts in native user-message
items. They were split between two native threads. The failure is therefore
not explained by a lost iOS composer request in the observed sequence.

After capture stopped, the original native trace contains 51 completed command
items, five file-change items, and six assistant-message items, including its
final answer. The original task continued for approximately 21 minutes after
Forge declared failure. Its work survives in the Lexi repository:

- `docs/forge/ios-endpoint-review-2026-09-08.md`
- `qa-evidence/forge-review/2026-09-08/`

Those are recovered outputs of the original task. Their reported iOS test counts
are the original agent's evidence and are not new simulator executions by this
investigation.

## Defects and corrections

### 1. Nullable free-text question crashes capture

Codex's native asynchronous question item permits omitted or null `options`.
The new adapter mapping handled a missing key but iterated an explicit null.
The earlier live acceptance case used two choices and therefore missed this
valid native shape.

Normalize absent/null options to an empty list. Regression tests drive the real
receiver through the question, subsequent assistant output, and the successful
terminal event; they also check explicit free-text answer routing and the
blocking-question variant.

### 2. Lazy restart replaces context and abandons the previous process

`start()` previously spawned again using only the construction-time resume ID.
A session originally created fresh had no such ID, even after `_thread_id` was
known. `_spawn_app_server()` replaced the process/socket references without
cleaning up the previous execution. A failed resume could additionally fall back
to a subprocess transport without its conversation history.

Recovery now pins the last known native thread, cleans up the old transport
resources before replacement, and resumes that thread. A failed resume surfaces
an error; it cannot silently select the stateless fallback. A mismatched resume
identity is rejected. Tests verify identity, cleanup ordering, absence of an
initial-prompt replay, and failure behavior.

### 3. Worker initialization can replace the root identity

A separate reproduced defect: `thread/started` carries its identity under
`params.thread.id`. The child-event filter checked only `params.threadId`, so a
worker initialization could overwrite the root used by subsequent prompts.
This was not the trigger in the observed incident. The fix recognizes the nested
identity and preserves child events without changing the root thread.

### 4. Terminal error detail disappears from reconstructed history

The receiver emitted an error `result`, but the shared result reducer discarded
its `is_error` and `error` fields. After previous prose, that looked like a normal
completed answer; an immediate failure without prose could disappear entirely.
The shared live/replay fold now retains terminal failure metadata and supplies
error content when there is no assistant content to preserve. Separate
regressions cover partial prose, immediate failure, and rebuild parity.

Current iOS live handling also needs a failure-aware `result` branch. It clears
errors and finalizes unconditionally rather than interpreting terminal failure
fields. Backend metadata fixes improve durable recovery; they do not alone
change that installed iOS behavior.

## Acceptance changes

The live catalog adds `question-freeform`: request a label without suggested
choices, deliver the controller's explicit answer, verify the resulting file,
and recall the previous task's sum and filename. It requires both question and
resolution frames. Codex live runs now fail if native initialization events
identify more than one thread, even when database replay parity passes.

The broader iOS audit found additional display/delivery risks, separate from the
confirmed context split:

- Codex tool rendering still assumes tools have no result frames, although the
  backend now emits paired results. New activity can prematurely settle other
  open tools.
- A late prior-turn terminal has no identity guard before settling current UI
  work. No such ordering was required to explain this incident.
- Question answers are marked sent before asynchronous WebSocket delivery is
  confirmed; the UI does not wait for `ask_user_resolved`.
- Ordinary composer sends use REST with a fixed Forge session identity.
  Navigation, reconnect, and display timeout do not themselves stop Codex.

The older stopped `lexi-ios-astra` session
`30557c09-0e45-4a11-a31c-8a324e732a40` has a separate September 6 failure:
its native API request was rejected with HTTP 401 for missing authentication.
That occurred before the September 7 server update and is not the null-question
incident above.

## Recovery boundary

Preserve both native threads and the original review artifacts. Restarting the
current affected session alone would resume its currently recorded replacement
thread; it cannot infer that the original thread is the intended conversation.
Reattaching that original thread or importing its missing history needs an
explicit recovery operation with provenance, not silent rewriting of the saved
stream. This investigation leaves the user's active session uninterrupted.

## Validation

- Focused regression gate: **306 passed**, including transport recovery,
  question parsing, failure metadata, live/rebuild parity, and corpus behavior.
- Broad Forge backend gate: **4,357 passed, 24 skipped, 62 deselected, 1 expected
  failure**; zero test failures. The gate remains red because Skuld coverage is
  **82.10%**, below the unchanged **85%** requirement. Evidence:
  `.forge-results/lexi-astra-regression-gate/unit-summary.json` and
  `unit-1.coverage.xml`. This is not a passing release gate.
- Real platform Astra canary: **2/2 scenarios passed** (`workspace`,
  `question-freeform`), session `f1536a13-c2cf-4e7d-aadc-d7860dacb92e`.
  It captured the free-text question, resolution, explicit answer, and earlier
  task recall without changing native thread identity. The test session stopped
  cleanly. Evidence: `.forge-results/lexi-astra-fix/codex-ea1823a663/`.
- Direct database audit: **159 raw rows → 153 public frames**, exact full replay
  **153/153**, cursor replay **76/76**, and all **151** observed non-seed live
  frames present in database order.
- Real native process-loss recovery: **passed in 11.74 seconds**. The test
  terminated only its own app-server, restarted the adapter on the exact same
  native thread, and recalled a random token without including it in the second
  request or using tools. Both test processes were cleaned up. This is an idle
  failure between turns, not proof of seamless mid-turn event recovery.
- Promoted a reviewed **77-frame free-text question fixture**, bringing the
  corpus to **12 fixtures**. All **22 corpus tests passed** after promotion.
- No new iOS simulator/device test was run by this investigation.

The fresh canary broker loaded the fixes from the working tree. The shared
platform and existing user brokers were not restarted; the affected session's
native identity and historical database rows were not rewritten. Restart and
explicit original-thread recovery remain separate deployment/recovery actions.

For recovery, the original thread ID above is the one that contains the review
request and its completed answer. The currently recorded replacement ID contains
only the follow-ups. A plain stop/start of the current Forge row uses that latter
ID; it will preserve the incomplete context unless the recovery explicitly
selects the original thread. Preserve the original row's transcript and both
native traces so recovered content can be attributed correctly.

## Original-thread import performed after investigation

At the user's subsequent request, the existing external-session import API
created and started a separate Forge session:

- Name: `lexi-ios-astra-recovered`.
- Forge ID: `b2a2aee6-f8ca-41ea-ae3a-bd9fa392c8e1`.
- Native ID: `01a07ff5-428f-7ac2-bf81-fa1d72503524`.
- Origin: `codex`; model: `gpt-6-astra`.
- Verified running status, successful iOS-facing socket connection, and native
  `system/init` identifying the original thread. The stored `cli_session_id`
  equals `external_session_id`.

The previous affected Forge session was left intact. No prompt was sent during
this initial import verification. At this stage native context resumed, but the
import flow had not copied prior native messages into the new Forge transcript:
its initial `/conversation` contained zero turns. The user's subsequent report
of an empty iOS view triggered the durable-history repair below.

## Durable-history repair deployed September 8

Recovery now imports a validated native snapshot into Forge's persistent event
log before returning success. Both Codex and Claude Code providers normalize
public messages and available tool activity; original timestamps and native
provenance are retained. Private reasoning and injected system context are
excluded. Snapshot-aware atomic markers prevent duplicate or misleading retries.

The repair also fixes two downstream failures discovered during validation:

- A later live canonical turn previously caused stopped-session rebuilds to
  discard the imported raw prefix. The rebuild now preserves it, including
  interrupted historical work that must not be finished by a later live result.
- The original reconnect snapshot exceeded iOS's default 1 MiB socket receive
  limit. Large snapshots now use the already supported shallow tool format,
  retaining all turns and tool references with full outputs fetched on demand.

The recovered session was stopped, backfilled through
`POST /api/v1/forge/sessions/{id}/history/import`, and resumed on the original
native ID. The platform main process changed from `1259029` to `2015580`; all
four unrelated active brokers retained their PIDs and process start times.
The healthcheck timer was suspended during maintenance and restored afterward,
along with the normal `KillMode=control-group` setting.

Live verification on `lexi-ios-astra-recovered`:

| Check | Observed result |
| --- | --- |
| Native history imported | 238 frames, complete snapshot |
| Conversation | 4 turns: 2 user turns and 2 assistant turns containing 10 native assistant messages |
| Tool activity | 112 unique calls with 112 matching outputs |
| Existing database prefix | All 10 prior rows unchanged |
| Atomic import boundary | Marker at seq 249; identical retry added zero frames |
| Public database/replay parity | 240/240 frames, exact payload equality |
| Running/cold history | Same turn IDs and complete text |
| iOS-sized socket test | 127,703-byte snapshot received with a 1,048,576-byte client cap |
| Lazy tool output | Largest output fetched through the public API; content exactly matched full history |
| Final database audit | 253 raw rows; one import marker; no duplicate canonical seeds |
| Native resume | `cli_session_id == external_session_id == 01a07ff5-428f-7ac2-bf81-fa1d72503524` |
| Model prompts during repair | Zero |

The first socket probe ran immediately after resume, before the new broker had
bound its listener, and received the facade's `4410` response. Broker startup
then hydrated all four turns, seeded the durable cursor at 249, and resumed the
correct Codex thread successfully. A subsequent socket probe passed. The early
startup response being labeled "no longer running" is a separate readiness
classification issue; this repair did not alter the session proxy's close codes.

Final broad validation: **4,500 passed, 24 skipped, 72 deselected, 1 expected
failure**, with zero assertion failures. Coverage is **82.23%**, below the
unchanged **85%** gate, so the broad release gate remains red. Native parser
checks separately achieved **88.54%** focused coverage. The atomic import suite
also passed **10 real PostgreSQL tests** using disposable schemas. No simulator
or physical iOS device test was run; the live socket test enforced the installed
client's receive-size limit, and its decoding/elision contracts were inspected.

Private source traces, before/after database snapshots, replay payloads, process
identities, and validation reports are retained under
`/tmp/forge-lexi-astra-investigation/`. The repeatable test and operator workflow
is documented in [native session recovery](forge-native-session-recovery.md).
