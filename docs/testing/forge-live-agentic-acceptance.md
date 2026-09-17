# Forge live agentic acceptance and replay corpus

This is the executable acceptance plan for remote Forge sessions through Skuld,
with Claude Code in tmux and Codex app-server as the active providers. It builds
on the [stability review](forge-stability-review-2026-09-07.md) and
[deterministic fault workflow](forge-stability-workflow.md).

The [native question acceptance extension](forge-native-question-acceptance.md)
adds actual iOS custom answers, restored drafts, multiple questions and checkbox
navigation, requiring native tool-result proof before a positive Forge receipt.

The goal is evidence that a real provider performed an action, Skuld captured
it, PostgreSQL retained it, and a reconnecting client can reconstruct it. A
plausible assistant answer is insufficient. A passing fixture does not certify
an entire deployment; the original run's failures remain in its provenance.

## 1. Active scope and retirement boundary

| Runtime | Status in this program | Selection |
|---|---|---|
| Claude Code / tmux | Required live acceptance | `skuldClaudeInteractive` |
| Codex / app-server | Required live acceptance | `skuldCodex` |
| Legacy Claude SDK, persistent subprocess, batch | Compatibility maintenance; no new live investigation | Existing unit/compatibility tests remain |
| Grok, Muse | Next provider expansion after these contracts settle | Existing fault tests remain |
| Native remote-control handoff runtimes | Separate product flow | These cannot be driven as ordinary broker chat |

The harness always sends an explicit definition. It does not change an existing
user's runtime defaults, migrate sessions, or remove the old transports.

The current live catalog is [scenarios.json](../../tests/fixtures/forge-live/scenarios.json).
Its provider models are explicit defaults and can be overridden at invocation.
Model availability is verified by actual execution, not inferred from a label.

## 2. What is built

- `python -m scripts.forge_live`: creates isolated synthetic workspaces and real
  sessions; observes the live socket; drives the scenario catalog; collects the
  persisted log; stops only the sessions it created; writes an honest manifest.
- `python -m scripts.forge_corpus inspect`: verifies a saved run against a
  session-scoped, read-only PostgreSQL query when configured; exercises the
  production fixture replay router; rebuilds turns; generates an offline Trace Lab.
- `python -m scripts.forge_corpus promote`: pins an attributed review to the
  capture hash and exports a selected scenario with expected turns and checkpoints.
- `python -m scripts.forge_corpus serve`: serves the reviewed corpus through the
  production replay WebSocket protocol, without invoking an LLM.
- Backend tests exercise missing output, false tool claims, ordering loss,
  duplicated completion, filtered pages, wrong answers, child lifecycle isolation,
  review mutation, and exact fixture replay. Corpus tests are part of `test-forge`.

The HTML Trace Lab opens as a local file. It offers scenario assertions, prompt
inspection, event search/filtering, sequence scrubbing, timed playback, raw frame
inspection, and audit findings. It uses no external assets or services. It is an
inspection tool, not an imitation of the iOS renderer.

## 3. Live scenario catalog

Each request gets a random completion marker. The runner waits until the marker
appears in assistant output followed by a terminal result. This also supports
Codex's empty `result` frame after streamed text. An interim parent response or
worker completion cannot advance the next scenario. The marker is never tool
execution evidence and must not be sent to workers.

| ID | Tier | Requested real behavior | Required evidence |
|---|---|---|---|
| workspace | Core | Read task/data; compute sum; write/read JSON | Paired command; output `FORGE_SUM=42`; independently read `answer.json == {"sum":42}` |
| search | Core | Search official Python JSON documentation; cite it | Structured native search invocation and result; assistant official-domain link |
| agents | Core | Spawn two workers; independent calculation and verification; wait for both | Two distinct native spawn receipts or agent identities; paired calls; final parent completion |
| recovery | Core | Command exits 7; separate successful recovery command | Error tool result plus both output markers; successful final result |
| presentation | Core | Prose, list, fenced Python, table, URL, Unicode | Captured assistant text contains required rendering features |
| continuity | Core | Disconnect/reconnect; recall prior result without tools | Correct remembered result after a fresh observer connection |
| plan | Extended | Maintain three native structured task states | A structured plan/task tool or native plan snapshot; prose checklist is insufficient |
| question | Extended | Ask blue/amber; explicitly choose second option; write choice | Captured question, controller answer, resulting `accent.json == {"accent":"amber"}` |
| question-freeform | Extended | Ask for free text with no options; consume answer; recall earlier context | Question with no choices, resolution, `label.json == {"label":"violet comet"}`, correct earlier sum and filename |
| long-output | Extended | 200 Unicode lines; successful silent command | Captured output, paired calls/results including empty output |
| background | Extended | Execute delayed command with observer disconnected | Completed command/result present in PostgreSQL and replay |

Core scenarios execute in one session to test accumulated context. Extended
scenarios follow the core set. `--scenarios` selects named cases in catalog order;
select `workspace` before cases that require its `answer.json`. Every repeat
creates a fresh workspace and session; a passing retry never erases a failure.

These are stochastic provider requests with deterministic observable contracts.
A provider declining an unavailable tool is a failed capability check, not a
skipped or passing scenario. Its explanation remains useful evidence.

Codex runs also require every native `system/init` to name the same thread.
A passing replay of the captured database is insufficient if recovery quietly
started a fresh native conversation. The September 8 Lexi Astra incident exposed
that failure alongside an unhandled `options: null` free-text question; both now
have explicit regression contracts.
See the [incident investigation](forge-lexi-astra-incident-2026-09-08.md) for the
native/database comparison and verified recovery limits.

An additional opt-in native recovery test terminates only its own app-server
after storing a random token in the conversation. It restarts the same adapter,
requires the same native thread ID, and verifies exact token recall without
tools or repeating the token in the follow-up. Run it separately from the
ordinary backend gate:

```bash
FORGE_LIVE_CLI=1 FORGE_CODEX_MODEL=gpt-6-astra \
  uv run python -m pytest tests/test_skuld/test_codex_recovery_e2e.py \
  -m live_cli --no-cov
```

This covers an idle process failure between two turns. Capture loss during
active execution and automatic reconstruction of missed native events remain
distinct recovery cases; the test does not claim to prove them.

## 4. Execution protocol

1. Allocate a uniquely named workspace containing only `TASK.md`, `numbers.json`,
   `CLAUDE.md`, `AGENTS.md`, and a new empty Git repository. No source checkout,
   credentials, or customer data are copied into it.
2. Write a manifest before launching. Persist the returned session ID immediately.
3. Wait for running status and message capability. Attach an observer using the
   platform session proxy and request internal-frame visibility.
4. For Claude, recognize the trust screen only when it names this invocation's
   exact synthetic workspace. Acknowledge that folder and wait for the real REPL.
   This is not a generic permission responder. Unknown permission gates stay visible.
5. Freeze the durable cursor before each prompt. Record the prompt, completion
   marker, observed actions, cursor interval, elapsed time, and every assertion.
6. On a timeout or closed CLI, preserve partial evidence and stop that session's
   remaining scenarios. Do not queue unrelated work behind a blocked model.
7. Stop the created session, fetch a stable head, drain the complete visible log,
   read the stopped conversation, and compare full and cursor WebSocket replays.
8. Independently inspect PostgreSQL and native provider evidence. Promote reviewed
   slices into the corpus; keep failing runs and unreconciled differences visible.

The runner has finite startup and per-scenario deadlines. It does not kill
unrelated processes, restart the shared platform, delete test sessions, or delete
workspaces. Cancellation should be followed by cleanup using the recorded IDs;
operator-owned process termination cannot promise application-level cleanup.

## 5. Running it

Run from the repository root with the project environment:

```bash
uv run python -m scripts.forge_live \
  --base-url http://127.0.0.1:8080 \
  --workspace-root /tmp/forge-live-workspaces \
  --output .forge-results/live

uv run python -m scripts.forge_live \
  --providers claude-tmux codex --extended --repeat 3 \
  --workspace-root /path/allowed-by-your-local-process-adapter \
  --output .forge-results/live-soak

uv run python -m scripts.forge_live \
  --providers codex --scenarios workspace agents question long-output background \
  --timeout 180 --output .forge-results/codex-canary
```

The workspace path must exist on the machine launching local sessions and fall
under its configured allowed mount prefixes. This first launcher targets mini /
local-process deployments. For Kubernetes, use an isolated fixture repository or
PVC source and a remote artifact reader; do not pretend a controller-local path
is mounted in a remote pod.

Use `FORGE_LIVE_TOKEN` for a bearer token when required. `--token-env` selects a
different environment variable; tokens are not command-line arguments or copied
into manifests. Provider CLI authentication belongs to the session runtime.

`--claude-model` and `--codex-model` override catalog defaults. A run records its
selected model/definition and current source hashes. Keep the deployment revision
separate from the controller revision when testing a remote installation.

```bash
# Supply the DSN through your environment/secret manager, then inspect ONE run.
uv run python -m scripts.forge_corpus inspect \
  .forge-results/live/<run-id> --database-url-env FORGE_TRACE_DATABASE_URL

# Without a DSN, fixture replay still works; database_verified remains false.
uv run python -m scripts.forge_corpus inspect .forge-results/live/<run-id>

# No provider account or running platform is needed for reviewed fixture playback.
uv run python -m scripts.forge_corpus serve --port 8767
```

A nonzero live exit status is expected until every required capability and the
public replay route pass on the target deployment. Do not add `|| true` to a gate.

## 6. Capture and replay invariants

### Action evidence

- Only typed tool-use/result blocks count as tool actions. A user echo, assistant
  claim, quoted JSON, shell process named "agent", or hyperlink does not count.
- Call/result IDs must pair, including unsuccessful and silent commands.
- Repeated stream/snapshot representations of the same ID count as one call.
- Agent spawn counts use distinct identities/receipts. Native waits do not count
  as additional spawns. Parent completion waits for the scenario marker.
- Tool output and assistant output are separate assertion sources.
- File assertions read the actual synthetic workspace after the provider finishes.

### Durable boundaries

- Sequences are increasing and belong to the requested session. `log_gap` and
  `log_conflict` make the capture fail.
- The REST endpoint filters some rows after querying a raw database page. A short
  or empty visible page is not necessarily EOF. The runner drains to a frozen
  durable head, including fully filtered pages.
- PostgreSQL inspection selects only one session ID inside a read-only transaction.
  Its wire projection must match the captured REST rows in order and content.
- Synthetic `conversation.turn` seeds and per-connect handshakes remain available
  to the database reducer, but are excluded from the public raw wire projection.
- Captured live events must appear in database order. Reconnect state snapshots
  may refer to an earlier identical persisted snapshot and are reconciled explicitly.
  This one-way check does not prove every database frame reached every live client.
- Full replay and a cursor tail must equal the HTTP log wire projection, including
  multiplicity. Comparisons never sort away an ordering defect.
- Child turn ends, usage, and assistant text must not finalize or overwrite parent
  state. Child-native events remain separately attributable in the capture.

### Truthful results

Track separately: scenario outcome; full deployment outcome; raw database
verification; live persistence; production fixture replay; public replay route;
manual/agent review; and device rendering. No one column implies all the others.

## 7. Agent review protocol

The reviewer reads the prompt, action evidence, tool arguments/results, worker
lifecycle, final assistant response, file outputs, and replay audit. The reviewer
must cite event sequences or exact IDs for each important observation.

The review answers these concrete questions:

1. Did the provider do what was asked, or describe doing it?
2. Do tool results match their originating calls? Are failed and empty results visible?
3. Are both worker identities present, are their results attributable, and did the
   parent wait? Did any child result appear as a parent terminal result?
4. Was the explicit second question option delivered? Does the resulting file
   agree with the answer rather than the UI default?
5. Does the final answer agree with independently checked artifacts?
6. Are there missing/reordered frames, duplication, phantom turns, or stale gates?
7. Do replay and the reconstructed transcript retain the same visible meaning?
8. Is the slice suitable for a portable fixture after explicit privacy review?

An agent may perform this review, but its narrative cannot overwrite failing
mechanical checks. Review artifacts identify the reviewer and bind to the exact
source frame hash. Negative fixtures require explicit expected failures.

Example review shape:

```json
{
  "status": "accepted",
  "reviewer": "named-human-or-agent",
  "source_frames_sha256": "sha256-from-audit.json",
  "notes": ["seq 20/24 pair command c1; observed sum=42; answer.json independently matches"],
  "expected_failures": [],
  "redactions": {"/absolute/private/workspace": "/fixture/workspace"}
}
```

```bash
uv run python -m scripts.forge_corpus promote .forge-results/live/<run-id> \
  --scenario workspace --review /path/to/review.json
```

Promotion requires database verification, fixture replay, unchanged evidence, a
reviewer, review notes, and a clean credential-pattern scan. The scan is a guard,
not a claim that arbitrary provider output is automatically anonymized. Literal
redactions change values, never event types, IDs, field names, or sequence numbers.
Source and exported hashes remain distinct. Review the resulting export before
committing it. Keep private raw bundles in `.forge-results`, which Git ignores.

## 8. Portable corpus contract for iOS

Each case consists of:

- `<case>.frames.json`: the existing `SessionLogEntry[]` array, with outer `seq`,
  `kind`, `session_id`, `ts`, and the original wire frame in `payload`.
- `<case>.expectations.json`: schema version, provider/model, source and export
  hashes, review, scenario checks, expected reduced turns, checkpoint sequence
  cursors, and the full run's remaining failures.

A fixture is a selected request interval. Checkpoints and expected turns are
computed within that interval, not against invisible earlier requests. A continuity
fixture may refer to earlier context in its prompt; use the full run for full-session
continuity behavior. `partial` is the batch reducer's interrupted-tail indicator,
not a claim that a live model at that checkpoint has failed.

The replay endpoint emits `payload` directly, one JSON object per WebSocket frame:

```text
ws://127.0.0.1:8767/api/v1/forge/replay/fixtures/<case>?speed=1&max_gap=2
```

Use `after=<seq>` for a tail; the default preamble supplies read-only capabilities
and reconstructed prefix history. For frame equality tests use
`preamble=false&show_internal=true&max_gap=0`. The server intentionally advertises
`send_message=false`: replay cards must not send real tool or permission responses.

In the nearby iOS repository, `packages/ForgeKit` owns `SessionSocket` tests and
`apps/chat/LexiChatTests` contains Forge replay and disk-cache tests. This change
exports compatible data from niuu; it does not modify that other checkout or claim
an iOS simulator run from this Linux environment.

### Required iOS replay matrix

For every reviewed fixture, run all applicable profiles below. Store fixture hash,
app build, device/OS, profile, test result, and screenshot/video references.

| Profile | Stimulus | Assertions |
|---|---|---|
| Decode | Feed every payload into the real decoder | No crash; unknown additive types remain ignorable; Unicode preserved |
| Natural timing | Replay emission timestamps at 1× | Streaming remains responsive; tool and text order stable |
| Burst | Replay at zero gaps | No lost content; one terminal state; no quadratic rendering stall |
| Slow stream | Pace tokens slowly | No premature empty/final turn; cancellation remains responsive |
| Tool in flight | Stop cursor after tool start | Running tool card and correct parent are visible |
| Tool finished | Advance through result | Card closes once; output/error retained; no phantom human bubble |
| Empty output | Complete successful silent tool | Card finishes rather than spinning forever |
| Question | Pause at question; advance after resolution | One answer card; second option selected; resolved card disappears |
| Agent fan-out | Pause after two spawns | Two distinct children under one parent; child activity cannot end parent |
| Agent join | Advance to completion | Both results retained; running badge removed; parent summary intact |
| Plan replacement | Apply successive plan snapshots | Replace whole list; statuses/counts match; empty/absent plan handled |
| Reconnect | Close/reopen at user/tool/question/agent boundaries | Prefix history plus tail equals uninterrupted state |
| App background | Suspend during tool; resume after result | Durable catch-up closes tool and restores final state |
| Cold launch | Clear in-memory state, keep cache, then reconnect | Cache and server reconcile without duplicate turns |
| Cache reset | Remove local session cache | Database replay reconstructs full selected history |
| Multiple devices | Two clients at different cursors | Both converge to the same final content and tool identities |
| Visibility | Internal frames hidden and then shown | Consistent toggle semantics; no unrelated state loss |
| Layout | Small/large phone, landscape, Dynamic Type, dark/light | Tables/code/tool output scroll correctly; actions remain accessible |
| Accessibility | VoiceOver traversal, large text, reduced motion | Reading order follows transcript; status changes have useful labels |
| Failure fixture | Replay a known incomplete/missing-capability case | Clear incomplete/error state; no false green success |

Browser session rendering can consume exactly the same corpus later. The Trace Lab
is useful for triage now, while product-specific screenshot expectations belong to
the product's own renderer tests.

## 9. Further live fault campaigns

These are planned extensions, not claims of automated execution in this first
catalog. Keep each bounded, use only created sessions, and preserve raw evidence.

| Campaign | Injection point | Required result |
|---|---|---|
| Mid-turn steering | After a long command starts | New instruction lands once; same-turn semantics match provider capability |
| Interrupt | Tool start / token stream / waiting worker | Bounded terminal interruption; no orphan spinner or tool subprocess |
| Cancel question | While native question is open | Explicit cancel or retained question; never silently select first option |
| Broker restart | After durable acknowledgement | Same provider conversation resumes; seq continues beyond stored head |
| Provider crash | Kill only the created CLI | One terminal error; persisted prefix and child cleanup remain inspectable |
| Persistence outage | Refuse log POST; restore before/after buffer limit | Retried data or explicit gap; no false durable success |
| Permanent rejection | Mid-run 401/403/409 | Visible terminal persistence fault; bounded retry policy |
| Slow observer | Artificially block one socket | Other clients and persistence continue within latency budget |
| Two active clients | Simultaneous steer/answer | Exactly one accepted operation per request ID; stale answers rejected |
| Resume imported session | Known native session ID | Earlier memory restored; no fresh-session fallback disguised as resume |
| Context compaction | Long synthetic history | No reader deadlock; truthful compaction start/end and preserved final turn |
| Tool cancellation | Failed child or timeout | Parent reports failure; child identity and partial result retained |
| Long soak | Many bounded fresh sessions and reconnects | No growing tasks, processes, memory, active agents, or unanswered gates |

Run baseline → injected fault → recovery → stopped replay for each campaign.
Budgets should include maximum session count, per-turn timeout, total runtime,
provider usage, workspace size, and artifact retention. Establish latency and
memory thresholds from measured baselines rather than inventing an unmeasured SLO.

## 10. Workflow and release decisions

| Cadence | Work | Gate |
|---|---|---|
| Every PR | Deterministic faults, corpus replay, broker/adapter tests, browser tests | No regressions; coverage policy remains unchanged |
| Explicit live run | Both providers, core catalog | All action checks plus persistence and public replay pass |
| Extended validation | Both providers, all supported capabilities | Fail unsupported required capabilities explicitly |
| Before transport/model upgrade | Repeat old and new versions with same catalog | Compare evidence and UI traces; investigate changed semantics |
| Release qualification | Reviewed corpus on iOS plus bounded live fault campaign | Product rendering and remote lifecycle pass together |

Live runs are intentionally separate from ordinary PR CI because they require a
running target platform, authenticated provider CLIs, and provider usage. A manual
workflow is provided for a runner configured with those prerequisites. It does not
start billing from untrusted pull-request execution.

Do not accept a release based solely on unit test counts, a prompt saying "done",
a green screenshot, a replay built only from mocks, or an overall score averaging
away a lost question/tool/agent event. Critical contracts are individual gates.

The measured first campaign and unresolved deployment differences are recorded in
[the live results report](forge-live-results-2026-09-07.md).
