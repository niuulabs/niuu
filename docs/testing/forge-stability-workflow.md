# Forge stability tests and release workflow

This is the execution companion to the
[2026-09-07 review](forge-stability-review-2026-09-07.md). It defines what must be
proved across Claude/tmux, Claude SDK modes, Codex, Grok and Muse, and separates
the gates implemented now from the acceptance work still required.

“Covered” below means a relevant executable test exists; it does not mean every
provider/deployment combination has passed that scenario. The review records the
actual runs. An unimplemented canary is not a green test.

## Running the implemented gates

Use the existing project environment (`uv sync --all-extras --dev`) and the locked
web workspace dependencies (`cd web-next && pnpm install --frozen-lockfile`).
Avoid invoking an unqualified system `pytest` from a different Python environment.

```bash
# Comprehensive Forge-facing unit/contract gate with an 85% Skuld coverage gate.
make test-forge

# Requires tmux; uses isolated servers and a fake Claude binary, no provider login.
make test-forge-tmux

# Repeat as independent required runs. Any failing run fails the whole command.
uv run python scripts/verify_forge.py tmux --repeat 10

# Real Chromium with mock services; separate from screenshot baselines.
cd web-next
pnpm exec playwright install chromium
pnpm exec playwright test --config=playwright.forge.config.ts
cd ..

# Forge's browser hook/component/HTTP/stream contracts.
make test-forge-web

# Fast iteration without coverage: still executes the complete unit selection.
uv run python scripts/verify_forge.py unit
```

The isolated coverage gate requires measured coverage of at least 85%. It does
not replace or reduce the existing backend/web coverage requirements.
Do not omit files, reduce the threshold or convert failures to skips to clear it.

`scripts/verify_forge.py` supports `unit`, `tmux`, `database`, `web`, `live-grok`,
and `live-muse`. It writes `.forge-results/` by default:
per-run JUnit, optional coverage XML, and a lane summary with command, revision,
elapsed time, exit status, test counts and skip reasons. Override the directory
with `--artifacts /path/to/run`. Old reports are removed before executing a run.

The required tmux/database/live lanes fail if no tests execute, if the command
fails, or if a test skips, including an expected failure. The former multiselect
exception was removed with the native-style checkbox regression; it now has to
execute and pass. There is no remaining tmux expected-failure allowlist.

Artifact uploads explicitly include the report directory because
[GitHub's upload action excludes hidden directories by default](https://github.com/actions/upload-artifact#uploading-hidden-files).
Uploads are restricted to the generated reports and browser test results.

### Database boundary

Use a **disposable, migrated test database**. These fixtures apply repository
migrations and create test data; a development/production database is not a test
fixture. The runner requires every connection setting to be supplied explicitly.

```bash
export TEST_DATABASE_HOST=127.0.0.1
export TEST_DATABASE_PORT=55432
export TEST_DATABASE_USER=volundr_test
export TEST_DATABASE_PASSWORD=volundr_test
export TEST_DATABASE_NAME=volundr_test
make test-forge-database
```

The values above are illustrative test credentials for a database provisioned at
that address. The command does not create that database. CI uses an ephemeral
PostgreSQL service. Local review evidence used a dedicated PostgreSQL 16 container
on an automatically allocated loopback port and removed it afterward.

### Live providers

Ordinary collection and unit CI must not invoke a real provider CLI. Live checks
require installed, authenticated runtimes in an isolated workspace and an
explicit opt-in:

```bash
FORGE_LIVE_CLI=1 uv run python scripts/verify_forge.py live-grok
FORGE_LIVE_CLI=1 uv run python scripts/verify_forge.py live-muse
```

These run existing real-CLI tests and can consume provider tokens. Missing
prerequisites fail an explicitly requested live gate. Muse's existing test also
accepts a correctly surfaced no-credential error; **that does not qualify as a
successful authenticated Muse canary**. A release canary must require an actual
successful turn and record it separately.

The [live agentic workflow](forge-live-agentic-acceptance.md) provides credentialed
Claude tmux/Codex scenarios through the served platform. The
[native question extension](forge-native-question-acceptance.md) adds rich answers
and actual iOS controls. Other-provider expansion remains separately scoped. Do
not use real tokens in pull-request workflows or point synthetic destructive
tool prompts at an existing user workspace.

## CI and release stages

| Stage                                | Trigger                                            | Checks                                                                                              | Evidence / gate                                                             |
| ------------------------------------ | -------------------------------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Local edit                           | Every transport/persistence/control change         | Small failing regression first; affected transport and boundary suites next.                        | Reproduction must fail before the fix and pass after it.                    |
| PR                                   | `forge-stability.yaml`                             | Python 3.11 and 3.12 unit/contract + coverage; real tmux; Forge web contracts; functional Chromium. | Required failures remain failures; upload reports even on failure.          |
| Existing monorepo CI                 | PR / dev                                           | Existing lint, complete package coverage, database, web, Helm/container jobs.                       | The new gate supplements these jobs.                                        |
| Scheduled repetition                 | Weekly in the new workflow                         | Ten independent tmux runs plus normal unit/browser checks.                                          | No retries that turn a flaky first run into an unqualified success.         |
| Provider canary — to implement       | Manual/protected environment and release candidate | All four actual provider runtimes, gateway, real broker, PostgreSQL and real browser/controller.    | Successful tool turn, restart/resume and HITL per runtime; record versions. |
| Deployment acceptance — to implement | Image/chart/credential/provisioner change          | Local, Docker, Kubernetes/Flux and externally registered sessions.                                  | Same protocol contract and durable history across each provisioning path.   |
| Soak — to implement                  | Before promoting a release candidate               | Long conversations, outages, multiple observers, memory/task/process accounting.                    | Resource bounds and recovery SLOs below.                                    |
| Promotion                            | All required evidence accepted                     | Review outstanding limitations, migration compatibility and rollback behavior.                      | No claim of complete parity while required cells remain missing.            |

The new workflow definition does not configure repository branch-protection rules
or make itself required in GitHub settings. Add its stable check names to the
applicable branch rules when adopting the workflow. Scheduled GitHub workflows
execute from the repository's default branch; the workflow must reach that branch
before its schedule becomes active.

## Shared acceptance invariants

1. **Session identity:** broker ID, platform session ID and native CLI session ID
   have explicit roles. Native resume must preserve actual model context.
2. **Message identity:** a caller's message/request ID survives delivery, steering,
   reconnect and durable reconstruction. Retrying an uncertain acknowledgment
   must not execute a task twice.
3. **Turn closure:** an accepted active turn has exactly one terminal outcome,
   including timeout, cancellation, provider failure and process death. The next
   message remains possible or receives an explicit unavailable result.
4. **Durability:** live delivery, in-memory capture and committed persistence are
   measured separately. Durable output is append-only; loss has an explicit gap.
5. **Transcript equality:** equivalent live, REST, stopped-session and replay
   views have the same stable turn IDs, text, tool pairs, status and attribution.
6. **Control identity:** an answer/approval acts only on the identified live gate.
   Stale IDs and unrepresentable answers fail explicitly.
7. **Capability truth:** an advertised control works on the configured runtime,
   or the capability is absent. UI visibility alone does not establish support.
8. **Isolation:** a failed/slow observer, another session, old socket or malformed
   frame cannot corrupt the target session's ordering or block its CLI reader.
9. **Bounded resources:** queues, tasks, retries, correlations, processes and
   temporary resources have defined ownership, capacity and teardown.
10. **Observable failure:** a failure has a category and correlation identifier;
    failed work is not reported as a successful turn or healthy persistence.

## Provider contract matrix

Run every applicable scenario below for the four primary runtimes. Keep Claude
SDK/persistent modes as compatibility cells; they exercise different interfaces
from Claude/tmux and must not be treated as interchangeable.

| Capability               | Claude/tmux                                                          | Codex WS                                                   | Grok ACP                                                   | Muse MSP                                                   |
| ------------------------ | -------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------- |
| Start and first turn     | Real tmux fake-CLI harness + unit hooks                              | Fake WS handshake/read loop                                | Fake stdio + opt-in real CLI tests                         | Fake MSP host + opt-in real CLI tests                      |
| Native resume            | Native Claude ID regression exists; real-version acceptance required | `thread/resume` tests; actual-context canary required      | Advertised but only resume hint: release blocker           | `session/resume` tests; fresh initial task must not replay |
| Steering                 | Native terminal input and hook correlation                           | Redirect/interrupt + correlated replacement                | Interrupt/resume                                           | Native `ifBusy: steer`                                     |
| Interrupt / timeout      | Watchdogs, hooks and keystrokes                                      | Control RPC + terminal status/EOF cleanup                  | Prompt timeout/finalization; bounded stop still needs work | Interrupt, grace/watchdog, EOF/stop finalization           |
| Permissions/questions    | Hook to structured gate to TTY selection; multiselect incomplete     | Native approval RPCs/dynamic tools                         | No general interactive permission capability advertised    | MSP approval and user-input requests                       |
| Tools/thinking/timing    | Hooks + MessageDisplay + pane fallback                               | Canonical tool blocks/results and output buffers           | ACP updates normalized to canonical blocks                 | MSP items/revisions normalized to canonical blocks         |
| Plan/agents              | Hook and pane state/reconnect tests                                  | Provider-specific tool/event mapping                       | Provider-specific tools                                    | Todo/subagent items                                        |
| Deployed binary and auth | Container/version/login canary required                              | Container/device login/app-server fallback canary required | Operator-supplied binary/login required                    | Binary/login/container installation proof required         |

## Scenario catalogue

Status terminology: **existing** = relevant regression suite is already in the
repository; **added** = this review adds executable coverage; **required** =
additional acceptance remains. Each scenario must name its provider/deployment
cell in the run report.

### A — Launch, identity and lifecycle

| ID  | Stimulus                                                     | Required observable result                                                      | Coverage / next work                                                                                 |
| --- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| A01 | Start a fresh session with no browser                        | Configured task starts once; all output can later be read.                      | Existing broker/initial-prompt tests; all-provider real canary required.                             |
| A02 | Two concurrent callers start a transport                     | One owned CLI/reader; callers see a ready or explicitly failed transport.       | Added Codex start race; existing Grok/Muse count tests do not prove both callers wait for readiness. |
| A03 | Missing CLI, failed preflight or malformed handshake         | Typed startup failure; no stale starting flag/process; retry possible.          | Added Grok/Muse spawn fault cases; extend real-image preflight.                                      |
| A04 | Cancel during spawn/handshake                                | Task cleanup completes; no leaked pipes/readers or permanent start flag.        | Added stdio cancellation; further handshake timing cells required.                                   |
| A05 | Restart a session with an initial task already executed      | Original task is not executed twice; native context remains available.          | Added Muse seed suppression; existing Claude native-ID/Codex resume tests.                           |
| A06 | Stop while idle, streaming, waiting for input and compacting | Bounded completion and exactly one terminal for active work.                    | Existing tmux/Muse + added Codex cleanup; Grok prompt-lock gap remains.                              |
| A07 | Kill broker or CLI during a tool call                        | Partial output reconstructs with interrupted/error state, then clean recovery.  | Existing crash/rebuild tests; process-death durable acceptance required.                             |
| A08 | Pod eviction / SIGTERM under load                            | Drain fits termination grace; remaining loss is reported explicitly.            | Required container/cluster test.                                                                     |
| A09 | Start local, Docker, Kubernetes, Flux and external sessions  | Same model/transport/credentials/launch definition; endpoint is reachable.      | Existing contributors/service tests; deployment acceptance required.                                 |
| A10 | Invalid readiness dependencies                               | Probe status reflects defined acceptance state, not merely an allocated object. | Required readiness redesign/test.                                                                    |

### B — Delivery, steering and control ordering

| ID  | Stimulus                                               | Required observable result                                                | Coverage / next work                                               |
| --- | ------------------------------------------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| B01 | Normal REST/WS send with request ID                    | Correlated queued/delivered/active state; one task execution.             | Existing delivery/steering tests.                                  |
| B02 | Disconnect before delivery ACK                         | Reconnect resolves the original ID; no automatic duplicate execution.     | Existing crash/steering tests; durable-ACK contract required.      |
| B03 | Duplicate message/retry from another observer          | Explicit deduplication or rejection using message identity.               | Required cross-client idempotency matrix.                          |
| B04 | Steer during text, tool output and provider retry      | Correct native or interrupt/resume routing; no lock stall.                | Existing tmux/Grok/Codex/Muse transport tests.                     |
| B05 | Burst of correlated steering messages                  | Every accepted message reaches active or failed; none remains orphaned.   | Existing coalescing/correlation tests; randomized timing required. |
| B06 | Send failure after queuing                             | `delivery_failed` persists and reconstructs; later work remains possible. | Existing delivery-failure/reducer tests.                           |
| B07 | Interrupt twice / after completion / during compaction | No duplicate terminal or interruption of an unrelated next turn.          | Existing controls; add four-engine late-control cases.             |
| B08 | Old WebSocket callback after session switch            | It cannot mutate current session data or reconnect the old session.       | Existing web socket hook tests; real browser fault case required.  |

### C — Transport failure and protocol handling

| ID  | Stimulus                                                          | Required observable result                                                                         | Coverage / next work                                          |
| --- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| C01 | EOF or socket error with active turn and RPC                      | Turn closes once; RPC fails immediately; active flag clears.                                       | Added Codex/Grok; existing Muse.                              |
| C02 | stdin/socket send fails before response wait                      | Pending RPC entry removed; turn terminal if work was active.                                       | Added Codex/Grok.                                             |
| C03 | Caller cancels waiting RPC                                        | No leaked future or orphaned correlation.                                                          | Added Codex; stdio prompt tests exist.                        |
| C04 | Non-JSON and non-object JSON before valid output                  | Invalid frame cannot discard later valid output.                                                   | Added Codex; Grok object guard, existing Muse guard.          |
| C05 | Valid JSON with invalid nested fields/oversized line              | Classify protocol failure; close or continue explicitly with bounded memory.                       | Required per-engine malformed-shape/fuzz cells.               |
| C06 | Unknown server request whose ID collides with an outgoing request | Respond unsupported; never resolve the wrong outgoing RPC.                                         | Existing Grok/Muse tests; extend all request methods.         |
| C07 | Response arrives after timeout/cancellation                       | No duplicate terminal or unbounded retained response entries.                                      | Required bounded late-response soak.                          |
| C08 | Provider rate limit / auth expiry / failed terminal               | Accurate failure/retry state; no success result for failed work.                                   | Added Codex terminal status; existing Muse/Grok typed errors. |
| C09 | Context-window exhaustion                                         | Reader continues processing compact RPC; one retry; terminal on failure.                           | Added real-reader Codex compaction and failure tests.         |
| C10 | Protocol/binary version changes                                   | Captured fixtures match identified runtime versions; unknown required capabilities fail preflight. | Required compatibility canary.                                |

### D — Durable capture and recovery

| ID  | Stimulus                                                  | Required observable result                                                          | Coverage / next work                                                                        |
| --- | --------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| D01 | Broadcast with zero, one or many observers                | Every semantic live event is captured first.                                        | Existing log-superset suite across real normalizers.                                        |
| D02 | Two flushes race                                          | No duplicate prefix acknowledgment removes unsent rows.                             | Added persistence race test.                                                                |
| D03 | Buffer overflows while POST is pending                    | New unsent sequences survive or appear in an accurate gap.                          | Added parameterized overflow test.                                                          |
| D04 | POST returns error, loses connection, or is cancelled     | Same unacknowledged batch remains retryable.                                        | Added persistence fault tests.                                                              |
| D05 | Remote ingest committed but response is lost              | Retry preserves canonical rows and cannot create conflicting payloads.              | Existing ingest conflict/idempotency tests; transport loss-after-commit injection required. |
| D06 | Output/overflow during head fetch                         | Rebased sequences and gap bounds are consistent.                                    | Existing capture-window tests + added overflow-gap test.                                    |
| D07 | Head returns 401/403/404/429/503 or invalid JSON/sequence | Agent cannot start in guessed sequence space.                                       | Added rejected/invalid-head tests; extend network/JSON decoding cell.                       |
| D08 | Mid-run ingest permanently rejects                        | Visible degraded/failed state; bounded behavior and recoverable queued data.        | Required policy/implementation, compare upstream `3978c6be`.                                |
| D09 | NUL/surrogate/Unicode in payload and dict key             | Whole batch persists; position-preserving sanitization; no Unicode corruption.      | Existing actual-PG tests; stale NUL expectation corrected.                                  |
| D10 | SIGKILL immediately after ACK, output and final result    | No accepted durable event disappears after restart.                                 | Required outbox/WAL design and process-death proof.                                         |
| D11 | Sustained backend outage beyond buffer capacity           | Explicit accurate gaps and bounded resources; operator sees the incident.           | Existing buffer tests; metrics/soak required.                                               |
| D12 | Shutdown while ingest is unavailable                      | Drain duration stays inside shutdown budget; evidence of undrained frames survives. | Required graceful-shutdown/outbox acceptance.                                               |

### E — Transcript/read-path equivalence

| ID  | Stimulus                                             | Required observable result                                                                  | Coverage / next work                                      |
| --- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| E01 | Read during a running turn                           | Same partial content, stable turn anchor and in-progress status as live view.               | Existing conversation/state tests.                        |
| E02 | Live, cold, stopped, archive and replay reads        | Stable IDs/text/tools/attribution/status across all paths.                                  | Existing reducer parity/roundtrip/rebuild suites.         |
| E03 | Incremental `after`/`after_id` around a mutable tail | No missing/duplicated turn at the seam; count alone does not prove identity.                | Existing FAULT-C/window tests; cache assertion corrected. |
| E04 | Frame ceiling and large conversation                 | Retain newest useful tail; declare truncated coverage.                                      | Existing durable-tail tests.                              |
| E05 | Text/thinking/tool call/result interleaving          | Ordered prose; paired tool IDs; no duplicate final content.                                 | Existing cross-transport normalized transcript tests.     |
| E06 | Tool output is huge or contains images/files         | Shallow/full parity; lazy details retrievable; input required by question UI is not elided. | Existing shallow/preview/file tests.                      |
| E07 | Repeated item/completion/replay delivery             | Idempotent terminal and tool result; no duplicate token accounting.                         | Existing examples; randomized provider matrix required.   |
| E08 | Muse view gap races live completion                  | Gap fill cannot reorder or append orphan content after a terminal.                          | Existing simple gap test; adversarial ordering required.  |
| E09 | Visibility gate changes                              | Internal tools stay appropriately hidden in every read surface.                             | Existing read/log gating tests.                           |
| E10 | Chronicle summary differs from raw event history     | Canonical conversation remains event-derived; summary is never substituted for lost turns.  | Existing chronicle-derived tests.                         |

### F — Human input, permissions, plans and agents

| ID  | Stimulus                                                  | Required observable result                                                | Coverage / next work                                                           |
| --- | --------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| F01 | Question arrives with no connected browser                | Reconnect surfaces an answerable, correctly correlated gate.              | Existing crash/question tests.                                                 |
| F02 | Answer arrives before menu rendering                      | Exact chosen option; no confirmation Enter selects the default.           | Existing real-tmux test now fixed and repeatedly exercised.                    |
| F03 | Stale/unknown request ID while a different gate exists    | Explicit rejection; unrelated gate remains pending.                       | Required tmux correction and all-engine contract.                              |
| F04 | Multiple observers answer the same gate                   | At most one accepted resolution; clear response to the losing answer.     | Required race coverage.                                                        |
| F05 | Multi-question, multiselect, free text and clarification  | Preserve all intended answers or report unsupported shape.                | Muse mapping tests exist; tmux multiselect remains strict xfail.               |
| F06 | Allow once, allow session, deny, expired approval         | Exact decision/scope reaches provider; no implicit broadening.            | Existing permissions/auto-approval tests; actual provider acceptance required. |
| F07 | Question/permission tool input exceeds elision limit      | Complete renderable input survives live and persisted reads.              | Existing AskUserQuestion/shallow regression.                                   |
| F08 | Plan mode attempts an edit                                | Actual runtime blocks or asks according to advertised policy.             | Existing live semantic test is not implemented; release limitation.            |
| F09 | Plan and agent updates with reconnect/restart             | Current plan and running/finished fleet are reconstructed truthfully.     | Existing plan/agent/pane tests.                                                |
| F10 | Teammate dies, tool attribution nests, usage arrives late | No ghost agents, incorrect parent tool or accumulating finished duration. | Existing per-agent usage/finished/pane regressions; add long-lived canary.     |

### G — Gateway, observers and deployment

| ID  | Stimulus                                                  | Required observable result                                                | Coverage / next work                                                     |
| --- | --------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| G01 | Slow or blocked observer while another reads              | Fast observer and CLI keep progressing; bounded queue/eviction.           | Required channel backpressure implementation.                            |
| G02 | Gateway restarts, JWT expires, browser sleeps             | Explicit auth/reconnect behavior; no false completed/dead state.          | Existing unit auth/socket tests; served-platform acceptance required.    |
| G03 | Cross-session/tenant log, file, preview or control access | Denied consistently through both direct and aggregate routes.             | Existing REST/auth/path tests; deployment authorization matrix required. |
| G04 | SSE heartbeat, session-created and stats events           | Timely HTTP stream events through real server; no gzip buffering.         | Existing real-PG/SSE and gzip tests.                                     |
| G05 | Flock trigger, gates, help request and terminal outcome   | One correlated workflow outcome, correct routing, no lost human response. | Existing `tests/test_flock` and room/mesh tests included in unit gate.   |
| G06 | Roll forward/back with schema/client version skew         | Compatible history/control fields; clear unsupported capabilities.        | Required container/binary/migration acceptance.                          |
| G07 | Hard reload and deep-link navigation                      | Same session surface reloads; filters/navigation stay usable.             | Added five real Chromium functional tests with mock services.            |
| G08 | Build uses stale package artifacts                        | Browser tests rebuild packages first and execute the intended source.     | Enforced in the dedicated Playwright config.                             |

## Fault harness requirements for the next implementation

Extend the existing `tests/support/forge` harness and queue-backed provider peers.
Do not create a second transcript reducer just for tests. Keep fault injection at
an external seam, then assert the production output/state.

- Use events/barriers to pause before a write, after remote commit, before an ACK,
  during head fetch and during gate rendering. Prefer these over arbitrary sleeps.
- Drive both notification-before-response and response-before-notification order.
- Give each run a unique session, workspace, native process/socket namespace and
  artifact directory. Kill only owned processes; never use `tmux kill-server`
  without a harness-owned socket or a blanket CLI process kill.
- Compare full canonical turns/IDs/tool pairs/status, not only substrings or event
  counts. For partial history, assert the explicit truncation/gap boundary too.
- Assert exactly one terminal **and** the ability to process a subsequent turn.
- Record cleanup: outstanding task count, open RPCs, process handles, pending
  gates, buffer depth and open tool calls after every injected failure.
- Use a deterministic random seed for event-order exploration, and persist the
  smallest failing trace as a regression fixture with runtime/version provenance.

## Canary and soak acceptance targets

These are proposed acceptance targets, not measured production SLOs. Measure and
revise them explicitly with workload evidence; do not silently widen timeouts
until a failing test passes.

| Exercise             | Starting target                                                                                                                           | Required evidence                                                                     |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Start/reconnect      | Ready or typed failure within configured startup budget; a reconnect rehydrates current state within 10 seconds on a healthy local stack. | End-to-end timestamps for gateway, broker, native session and durable head.           |
| Terminal correctness | Exactly one terminal per started turn across every exit path.                                                                             | Turn ID, terminal status and state after next message.                                |
| Durable acceptance   | Zero lost accepted-durable messages/results after forced restart.                                                                         | Before/after committed sequences and canonical transcript equality.                   |
| Observer isolation   | A blocked observer does not stop other observers or delay native RPC responses beyond their budgets.                                      | Per-client queue depth, eviction reason, frame ordering.                              |
| Continuous session   | 1,000 turns and 100 reconnects per provider with mixed tools/questions/errors.                                                            | Memory trend after warmup; task/process/RPC counts return to baseline.                |
| Backend outage       | 30-second, 5-minute and over-capacity outages followed by recovery.                                                                       | Exact retained range, explicit gaps, no seq collisions, no duplicate execution.       |
| Shutdown             | Broker drain/CLI cleanup fits the deployed termination grace (currently 30 seconds in the chart).                                         | No orphan owned process; persisted shutdown outcome or explicit recoverable backlog.  |
| Version skew         | Current CLI, previously accepted CLI, current UI and previous UI.                                                                         | Capability compatibility, resume history, question/control schemas, no false success. |

## Incident workflow

1. Preserve the failing session's IDs, runtime versions, image/build identifiers,
   timestamps and committed log head before restarting it.
2. Classify the failure: launch/auth, delivery, native turn, gate, channel,
   persistence, reducer/read path, provisioning or workflow routing.
3. Check whether the apparent success was a send ACK, in-memory capture, persisted
   ingest, native terminal or derived summary. Do not treat them as equivalent.
4. Reproduce at the narrowest external seam using a scrubbed trace. Keep a failing
   test before changing code; broaden to the shared provider contract afterward.
5. Verify current branch and integration-branch behavior separately. Adapt fixes
   across extracted module boundaries without discarding independent changes.
6. Rerun the affected gate, then the full Forge lanes justified by the change.
   Retain the first failure, fix, final results, skips and version provenance.
7. Close the incident only when its acceptance assertion passes at the boundary
   where it failed. “The process restarted” is not proof that accepted work or
   the conversation survived.
