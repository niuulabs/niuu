# Codex / local Forge release readiness

**Spark activated; Thor not deployed.** The frozen candidate was activated on
Spark at 22:00 UTC on September 13, 2026, following fresh preservation checks and
a newly announced Spark-only window. See the [Spark deployment checkpoint](codex-spark-deployment-20260913.md)
for observed results and limits. No session gateway/native owner, database,
Voice/Live/dictation service or web preview was restarted; no prompt was resent.

The preparation evidence below is the historical **21:33 UTC** checkpoint, not a
claim that those older snapshots authorized the later cutover. Thor remains on
its restored baseline and needs separate operational review before any deployment.

## Frozen release pair

| Artifact | Git revision | Python source SHA-256 |
| --- | --- | --- |
| Codex candidate | `f5d7a5e49eafd765f8daf53bdc51c95ac1949939` | `47dfd370523fefaabcf9189808495a942c9548d3c148203d4251ab15907c42a1` |
| Safety-only rollback | `e8c814f854beb30d00d068622d63a3c3ab244976` | `068cb62412746da3e46cedc180f48365577f723c9d9aff994341cd7d29faeae5` |

Both hosts have both sources and **separate copied environments**:

- Thor: `/home/thor/.local/share/niuu/releases/forge-codex-ready-20260913-v2`
  and sibling `forge-local-preservation-c6d80980`.
- Spark: `/home/xteo/.local/share/niuu/releases/forge-codex-ready-20260913-v2`
  and sibling `forge-local-preservation-c6d80980`.

The rollback is the previous Thor baseline plus the isolated backend safety repair,
not the full Codex feature upgrade. Both candidate and rollback therefore retain
local processes during startup. Their import paths/source digests were checked in
their own environments; dependency versions equal the previous staged environment
on each respective host. No shared environment or native CLI/auth installation
was changed. The candidate retains the exact Project UX input `11e9e0c` already
agreed for this mission; no moving project-instruction integration was merged.

## What is better for the user

The [release notes](codex-release-notes-20260913.md) explain the implemented Codex
improvements: visible assistant text between tools; exact command/output/error
fidelity; native same-turn steering; better pending approval/question handling;
validated model/effort/service-tier controls; and identity-preserving recovery
without silent execution-path fallback. Slash expansion is explicitly deferred.
Optional iOS work and reconnect acceptance guidance are [documented separately](codex-ios-runtime-handoff.md).

## Validation

- **Thor, final staged candidate:** 5,015 passed, 24 skipped, 95 deselected,
  one expected failure; no failures or reported pytest warnings. Aggregate
  statement/branch coverage **85.98%** over the 11 changed production modules plus
  the deployment script, retaining the 85% gate. This is not whole-repository or
  per-file 85% coverage. Imports were audited against the exact staged source.
- **Deployment orchestration:** 28 tests passed; script-specific statement/branch
  coverage 97.02%. Failed guards prevent all later install/restart operations;
  candidate failures use only the audited safe rollback. Both readiness paths
  poll with bounded deadlines. Stable HTTP/TLS checks replace dynamic body hashes.
- **Full local API restart/reconnection:** four stored-status cases (running,
  stopped, archived, failed) passed against candidate and safety-only rollback on
  **both hosts**. Each executes real API shutdown/startup, abrupt API death, state
  recovery, periodic reconciliation and production HTTP/WebSocket reconnect.
  Gateway/native-fixture identities survive, the same turn continues, output
  generated while the API is down is replayed, and exactly one native send occurs.
  Model and database infrastructure are explicitly synthetic/mocked; no provider
  calls or production database operations are part of these tests.
- **Spark, final staged candidate:** 370 passed across Codex transport/alignment,
  runtime options, event logging, local processes, backend composition and release
  guards/restart cases. Its separately staged rollback passed all four full API
  restart cases. Do not add overlapping host test totals into a unique-test count.
- **Real isolated tmux, fake Claude CLI:** five reconnect/crash/question/cursor
  cases passed on final staged source. The first run exposed an outdated test
  `FakeWebSocket` lacking `query_params`, not a failure of the real WebSocket
  implementation. That fixture was repaired in the candidate and retested.
  Temporary homes/sockets and a fake CLI prevented touching real credentials or
  unrelated tmux owners. Retained native-fixture PID records confirm cleanup.
- The same full startup regression **rejects the original source's Kubernetes
  misclassification**, rather than merely testing a stop-method mock. Earlier
  native Codex provider acceptance remains separate dated evidence; no new native
  provider acceptance or physical-phone test is claimed here.

## Read-only host preparation

Final guarded preparation passed on Thor at **21:32:47 UTC** and Spark at
**21:32:49 UTC**. Private manifests have **`window: null`**: they cannot be applied
until a new window is set and preparation repeated for that exact manifest.

| Host | Archived-inclusive identities | Live gateways | Protected process identities | Exact inactive replay samples |
| --- | --- | --- | --- | --- |
| Thor | 629 | 9 | 30 | Share with Voice 20 turns; Dictation 96 turns |
| Spark | 12 | 0 | 4 | 4 and 2 turns |

Spark's 12 is the correct archived-inclusive inventory; the earlier ordinary
nine-session enumeration is not proof that three sessions were newly created.
On Thor, two additional native Codex owner processes appeared between preparation
snapshots. All original 28 owners, gateways, stored metadata, selected histories,
unit and configuration hashes remained unchanged. The strict whole-inventory
comparison correctly noticed those additions; the final snapshot protects all 30.
They were not attributed to a particular task, stopped or restarted. This is not
a global-quiescence claim: concurrent changes require fresh prechecks at cutover.

Postmaster identities are explicitly protected, distinct from transient database
connection children. Trusted TLS health on port 9501, HTTP health on 9500 and
unchanged static preview content on 5300 passed. No TLS exception was used.

**At preparation time:** Thor was the healthy restored old API (documentation
`c6d80980` over the original `44f85dc3` code); Spark retained the earlier `72f8fdbe`
canary. The later Spark deployment is recorded separately above. The three
older sessions terminated in the incident were not automatically resumed. Their
saved history remains separate from unprovable prior unflushed state.

## Cutover and adoption

Use the [guarded release procedure](local-api-release.md), not the expired incident
script/window. The coordinator owns timing/conflict reconciliation. Immediately
before the newly announced window, refresh inventories/source/unit/independent
health/replay checks on both hosts; any failed guard halts before mutation.

API cutover keeps existing gateways and native turns alive. Clients may reconnect
their proxy sockets; **no prompt resend is required or authorized**. Existing
gateways stay on their loaded code. New sessions use the candidate; upgrading an
existing gateway requires its owner's saved-boundary transition with the same
Forge/native identity, workspace/history and explicit pending-control disposition.
This package performs neither owner recovery nor gateway migration automatically.

Evidence: [curated release-preparation manifest](codex-release-preparation-evidence-20260913.json).
Raw private evidence is in `.local/codex-release-preparation-20260913/` in the
worker checkout and `/home/xteo/.local/share/niuu/release-preparation-20260913/` on
Spark. Original incident/failed-test/earlier-preparation records remain retained,
not overwritten by this readiness checkpoint.
