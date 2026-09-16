# Forge integration reconciliation — 9 September 2026

This reconciliation folds the released Forge reliability work into the modular integration architecture and includes the reviewed upstream development through `629b4ca6`. Development continues on local `dev-integration`, with explicit tracking of `origin/forge/dev-integration`. This is an integration promotion; production deployment is a separate operation.

## History and branch ownership

| Role | Pinned revision | Treatment |
| --- | --- | --- |
| Integration parent | `df1566e6204a79477c22078c2a70ad8170153564` | Reconciliation starts here; final promotion is a fast-forward. |
| Released Forge source | `596cacb54d60f649ebb9747f9e526b63cae74b0e` | Behavior and regression coverage retained. |
| Complete release branch | `103f14757179a4f222dac6f3a146a58699a64b01` | Includes final release acceptance documentation. |
| Reviewed upstream | `629b4ca6f4198aae587589346fc196caea683fa1` | Merged after the released behavior was reconciled. A fresh fetch confirmed the same upstream head. |
| Release reconciliation | `8278c4b9` | Resolves 85 initially conflicting paths. |
| Upstream integration | `b08bed86` | Preserves released event-log safety while accepting upstream runtime changes. |
| Validation fixes | `432192e7`, `06235253` | Lifecycle, configuration, migration packaging, resource ownership, and required database coverage. |

The reconciliation branch is `reconcile/forge-dev-20260909`. The active integration parent previously matched `origin/lexi/ravn-rooms` and had no remote tracking. The separately named `origin/dev-integration` is a June branch at `11cc4e41`, with nine commits outside this ancestry. It is preserved. The new Forge-specific remote branch avoids force-pushing or silently repurposing that historical line. `origin/lexi/ravn-rooms`, release branches, and `main` retain their previous roles.

## Reconciled behavior

The integration branch's module boundaries remain in place: the broker uses the split event-log, transport lifecycle, API, WebSocket, and activity modules; shared transcript projection and chat hooks serve both protocol consumers and UI. Reconciliation was performed at the behavior level because replacing the newer modules with the older monolithic broker would discard platform changes.

The resulting integration retains:

- Serialized event-log flushes, sequence-based acknowledgements, durable delivery claims, idempotent dispatch, and explicit errors for rejected writes. Upstream permanent-rejection handling is combined with the released flush algorithm; count-based deletion of a concurrently changed buffer is excluded.
- Native Codex app-server sessions over the private Unix control socket, bounded large frames, native session identity, recovery import, control tombstones, and correlated completion/error behavior.
- Ordered public text items, tool calls/results, stable text identities, shallow/lazy tool results, completed-turn repair, and consistent database/WebSocket replay projection.
- Native Claude tmux tools, agent and plan surfacing, questions with native consumption evidence, and native prose/tool chronology.
- Configuration-backed transport settings and shared catalog support for the retained active runtimes. Deprecated Claude SDK/remote modes remain compatibility surfaces; they are outside the live acceptance focus.

Reviewed upstream room routing, Bifrost, and workload identity changes are included in the platform merge. A real Bifrost override mismatch was repaired: its header method now accepts and forwards the model argument expected by the shared Anthropic adapter. Obsolete automatic-merge defaults for unsupported Codex remote control and a nonexistent Grok build model were removed; the current Astra, Sol, and Terra catalog entries remain.

## Migration compatibility

Migration numbering had diverged at 49. The canonical integration/upstream stream is retained through 62. Released delivery claims, removal of the obsolete duplicate index, and the startup ledger are assigned canonical migrations 63–65.

`migrations/lineage-aliases.json` records exact filename/checksum aliases for identical released SQL. The shared PostgreSQL startup runner verifies immutable checksums and adopts aliases by appending canonical ledger entries. It does not rewrite old applied timestamps, checksums, event-log rows, or the numeric migration cursor. DDL and ledger updates remain transactional and startup failure remains visible.

The Helm bridge handles the released and integration numeric histories around versions 49–55 by adding the missing canonical prerequisites before the ordinary migration container continues. Dirty numeric histories remain errors. Source migrations, packaged CLI migrations, the Helm ConfigMap, alias data, and the bridge init container are checked together. Validation caught and fixed a malformed generated ConfigMap where SQL had accidentally been placed under metadata.

The real PostgreSQL cases verify fresh startup, repeated startup, both historical lineages, immutable old rows and ledger entries, rollback, concurrent delivery claims, and fail-on-error behavior. The standard Forge database lane now includes the lineage and delivery-contention tests so they cannot be omitted from future required runs.

## Live Claude finding and regression

A real agentic campaign exposed a missing finalization boundary. A late worker idle reply completed after the next user request had already been submitted. Claude then executed the next request and displayed its final answer, but emitted an `idle_prompt` notification without a main Stop hook. Forge had also started the new semantic turn without rearming its completion watchdog. The answer remained in progress instead of becoming a completed persistent turn.

The transport now rearms completion tracking when native activity begins after a completed turn. Recovery from a missing Stop requires a finalized main-session MessageDisplay with the same nonempty native prompt identity as the idle notification. Partial display buffers, outstanding tools, queued delivery, native questions, foreign prompts, and child sessions prevent this fallback. A duplicate notification or delayed matching Stop cannot commit the answer twice. Ordinary terminal silence still does not imply successful completion.

The recorded failing hook sequence is retained as `tests/fixtures/forge-live/claude-queued-idle.json`. Regression tests cover that sequence, timeout rearming, rejected ambiguous completion evidence, and duplicate finalization. The affected tmux/interleaving/question suite passes 137 tests. Fresh Claude campaigns subsequently completed workspace commands, worker agents, intentional tool failure/recovery, and native questions.

## Acceptance evidence

| Gate | Result |
| --- | --- |
| Full backend, Python 3.13.13, warnings as errors | 19,391 passed; 37 skipped, 164 deselected, one existing expected failure; 86.47% combined line/branch coverage. |
| Required real PostgreSQL lane | 48 passed, zero skipped; PostgreSQL 16, fresh disposable database, including 12 lineage/delivery checks. |
| Real tmux harness | 57 passed; the affected transport/text/question regression suite additionally passes 137 tests. |
| Full web coverage | 6,034 tests across 408 files; statements 93.03%, branches 85.39%, functions 92.45%, lines 94.58%. |
| Web build, typecheck, lint, format | Passed; publication also executes the repository pre-push build/typecheck/test/format hooks. |
| Functional Chromium | Five Forge stability cases plus one interleaving/replay repair case passed. |
| Native Codex Astra | Workspace, worker agents, intentional failure/recovery, native question, interleaving, resume, and native import passed. |
| Native Claude Fable 5.1, xhigh | Same active scenarios passed, including a fresh pinned campaign at `06235253`. |
| Database / public / WebSocket replay | Matching ordered streams for both interleaving canaries before and after resume. |
| iOS simulator | Recovered Claude/Codex interleaving and Codex process-reopen replay passed with build 2241. |

All live campaigns use synthetic workspaces and disposable databases. Original failed evidence is retained alongside successful reruns. Harness incidents are distinguished from product findings: an initial collector setup used the wrong endpoint, a candidate restart briefly overlapped the draining previous server, and the old integration helper was invoked a second time on an already migrated disposable database. The accepted database run uses a fresh database as required by that helper. Independent schema-lineage tests explicitly cover repeat startup through the production runner.

Resource checks also exposed unclosed test-owned OpenClaw and Sleipnir SQLite stores, the OIDC login callback listener, and short-lived tracker connection-test adapters. Their owners now close them. Tracker endpoint tests use controlled responses rather than depending on the public network. Warnings are treated as errors in the accepted backend gate.

### Native replay and recovery

Both interleaving canaries require the actual model to emit commentary A, execute a command, emit commentary B, execute a second command, and produce a final Markdown answer containing a list and Unicode code block. The recorded sequences contain three separate public text parts. Claude has two tool call/result pairs; Codex exposes four pairs because orchestration and command execution are separate native tools. Database/public-log and replay comparisons pass for both.

The canaries were stopped and resumed without another user prompt. Native identities and exact public text were preserved. They were then stopped and imported into new Forge sessions using the native IDs. The imported sessions had persistent content before any restart: Claude had 10 database rows and Codex 14, each with two conversation turns and three assistant text parts. Repeated import requests added zero rows.

| Provider | Original Forge session | Native identity | Recovered Forge session |
| --- | --- | --- | --- |
| Claude tmux | `61ce3075-e06e-4f5a-b0ba-c0bcd65f10db` | `5b1b43df-de07-4f79-a704-a3327059d937` | `ed05e5c4-f140-4bba-94d3-78890cca345b` |
| Codex Astra | `0fefb00b-5338-4282-9a8a-67d68d149828` | `01a085b8-17df-76a2-b178-457aa19747b0` | `94461a5d-e222-406f-bf1d-4950251ce2f1` |

### iOS simulator

Acceptance uses Lexi iOS main `262d897683639aab415deb0883c41f346ed2a675`, installed build 2241. The debug library SHA-256 is `28a71150d5f4e1215161cb4ed341d1be6b8aa4edfe4eaa6fc5473e550a1261f5`. No iOS source changes were needed for this reconciliation.

The final checks run on iPhone 17 Pro/iOS 26.2 in a dedicated simulator device set, UDID `483954B7-2FD1-4C84-8541-4AB326555008`. The app connects to the disposable candidate through an explicit process-environment instance and SSH tunnel. Screenshots and accessibility assertions verify A → tools → B → tools → final, the recovered-history indicator, Markdown/code rendering, and Codex replay after terminating and reopening the app. There was no physical phone testing.

## Repeating and promoting

Use [the stability workflow](forge-stability-workflow.md), [live agentic acceptance](forge-live-agentic-acceptance.md), and [native recovery](forge-native-session-recovery.md) together. Keep provider tests in owned workspaces, use a fresh disposable database for the integration fixture, and supply the history database URL for the isolated import/lineage cases. Run the full backend with `-W error`, the required Forge database and tmux lanes, web coverage/typecheck/build, and functional Playwright checks. Record source revisions and failed runs rather than replacing their evidence with successful retries.

After the gates pass, fast-forward the local integration worktree to the reconciliation result and publish `forge/dev-integration` without force. Configure `dev-integration` to track that exact remote ref. Future development branches should start from the tracked integration branch; fetch and review upstream before each reconciliation. Migration lineage changes must retain the compatibility tests and checksum aliases.

This acceptance covers local-process Forge with authenticated Claude tmux and Codex, PostgreSQL, browser regression fixtures, and iOS simulator replay. It does not certify live Grok/Muse behavior, Kubernetes deployment, production database rollout, exhaustive outage/soak testing, or physical-device behavior. Existing skips/expected failures are recorded in the backend report. Integration promotion does not claim a faultless system or deploy the production server.

Private evidence is retained at `/home/thor/.niuu/validation/forge-reconcile-20260909`: pinned refs, merge inventories, test/JUnit/coverage reports, native campaign manifests, raw/public/replay comparisons, import proofs, simulator screenshots, accessibility assertions, and the publication result. Raw live captures and private remote fetch logs are not committed.
