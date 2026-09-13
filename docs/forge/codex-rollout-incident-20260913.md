# Codex alignment rollout: split deployment and Thor preservation incident

## Status

**Thor rollout is halted and not accepted. No further restart, owner-session
restart, or message resend is authorized by this incident checkpoint.** Spark's
API canary is deployed. This is not complete Codex parity or an upgrade of all
live Skuld gateways. The latest coordinator receipt requires isolated cause,
impact and recovery work before any revised rollout window.

| Host | Current API source | Disposition |
| --- | --- | --- |
| Spark | `72f8fdbe0389a1568fed7d739e60d9fc39c1991e`, source `e98d5b81160c442d77aa846e73ec96ee35e0d9910fab63ae02fd6306b891ce2b` | Healthy canary, no failed plugins, 9 stored sessions unchanged. |
| Thor | `c6d80980c304f972461d9e31d36ac3aeca35cc8d`, documentation over the original `44f85dc3` code; source `e34593b7c6a10add6e50d62d2f9f3202a940f9d14bdf4819a26761d27610b7b0` | Source-only rollback verified healthy; 9 surviving gateways keep their original process identities/source. |

The different Thor revision after rollback is the existing documentation HEAD,
not different Python code: the source digest equals the pre-maintenance digest.
No live checkout or shared virtual environment was edited. Detached release
checkouts and separate copies of each host's existing environment were staged.
The only installed runtime override remaining is Spark's owned source-only
`zzzz-codex-runtime-20260913.conf`; Thor's was removed during rollback.

## Timeline (September 13, UTC)

- **17:42:41:** announced 17:45–17:55 window, Spark first; parent later confirmed
  no Lexi conflict. The attempted Physics informational receipt failed the
  cross-project boundary with 422: **no Physics delivery/acknowledgement occurred**.
- **17:46:25:** first Spark precheck used loopback, which its Tailscale-only bind
  did not serve. It failed before any unit change. The address was corrected to
  the configured host alias; this failed probe is retained, not acceptance proof.
- **17:46:46–17:47:03:** Spark source-only restart and verified candidate health.
  Session/log/sample-replay/project checks passed before proceeding toward Thor.
- **17:47:57:** Thor restart requested after exact unit/source/gateway checks.
- **17:48:12:** old API finished shutdown. Systemd explicitly reported all three
  subsequently lost gateways still running and ignored them as leftover
  processes when starting the candidate. This contradicts a blanket systemd
  child-kill explanation.
- **17:48:15:** candidate constructed `LocalProcessPodManager`, entered startup
  reconciliation. The lia-physics-gravity gateway began graceful shutdown.
- **17:48:20 / 17:48:24:** agents-brain / lexi-physics-ios-upgrade gateways began
  graceful shutdown. Their logs recorded session-ended and workspace archives.
- **17:48:27:** candidate health succeeded, but the first missing gateway failed
  the preservation guard. The script removed only its own source override and
  restarted the original API. Its immediate rollback health read was too early
  and recorded connection refusal; subsequent independent readback verified the
  original source healthy. A healthy rollback did **not** restore lost processes.
- Parent incident review `21483c5a-ca34-416f-a08f-c26085a1a1ea` confirms the split
  host versions and requires Thor to remain halted.

## Cause and validation gap

The existing `_runtime_backend(settings)` helper inferred Kubernetes whenever
`Settings.mode` was absent. Volundr `Settings` has no such field. Consequently a
real `LocalProcessPodManager`, including mini-mode configuration, was classified
as Kubernetes. That backend intentionally includes stopped/archived rows in
startup reconciliation and calls `pod_manager.stop()` on their live runtimes.

All three lost gateways had **stored status `stopped` before the rollout**, even
though their processes and direct health checks were live. The candidate did not
change the classification or cleanup functions; restarting exposed this existing
composition defect. Source-path analysis, the sequential shutdown timeline and
an offline reproduction support this cause. The reproduction invokes the actual
old classification and `SessionService.reconcile_active_sessions`, with only
infrastructure mocked: two terminal rows produced two stop calls.

The original OS/asyncio probe correctly demonstrated child survival across parent
shutdown. It did **not** exercise the new API's application-level startup cleanup.
The earlier local-process tests likewise omitted composition-to-reconciliation
coverage. Treating those checks as sufficient protection was a validation error.

An isolated repair, source commit `c658b1d88ee6fad4df5cf2f8396573aba51da93a`,
makes the `PodManager` port expose its backend semantics;
`LocalProcessPodManager` declares `process`, and composition passes the actual
adapter into backend selection. Legacy Kubernetes/OpenShell behavior is retained.
No class-name routing is added for custom process adapters. New tests cover actual
local composition, stopped/archived/failed row preservation, continued running-row
reconciliation, custom adapters and legacy backends. This repair is **not deployed**. Seven new regressions and the composition,
liveness, local-process, main and service tests pass (190 targeted tests). The
expanded exact-source sweep passes **4,983 tests**, with 24 skipped, 95 deselected,
and one expected failure; no failures or reported warnings. All 11 production
files changed by this runner are in the statement/branch coverage scope:
**85.67%** aggregate, unchanged 85% gate. This is not whole-repository or per-file
85% coverage. Loaded module paths were audited against the isolated checkout.

## Affected gateways and native runtime impact

| Stored session | Gateway PID | Native tmux PID | Retained Forge turns | Native CLI identity |
| --- | --- | --- | --- | --- |
| lexi-physics-ios-upgrade `e1bcc8ca-3870-4fca-b8e4-441c3660b5c0` | 2594685 | 2594793 | 191 | `b9e084e9-69d8-45ae-b82f-837ddcf78cef` |
| agents-brain `c29f623f-9417-451c-a884-b921534a6f41` | 3538876 | 3538994 | 94 | `8f38aa76-b3ae-4e68-95b4-e17bb504fb75` |
| lia-physics-gravity `f952f335-f8dd-4e60-bf62-7ff2ebc04eb2` | 3003963 | 3004029 | 34 | `1d9afa79-ff85-40d2-b5c6-cdeb2e5838ac` |

All six PIDs are gone. Broker shutdown calls transport stop; the tmux transport
terminates its native session. Thus reporting “only gateways affected” would be
incorrect. The three native transcript files remain present and parseable, with
23,065 / 6,401 / 3,748 records respectively. Those counts describe retained files,
not completed user turns or proof of full recovery. An older independent tmux
server, PID 819063, survived.

The 9 remaining gateways, including the 5 currently active Physics sessions,
coordinator, runtime runner, UX and web runner, retained their original PID/start
identities and direct health. None were intentionally restarted, resumed or sent
replacement user prompts. No other native-owner files were edited.

## PostgreSQL and independent services

The database **postmasters** are unchanged: Docker `volundr-pg` PID 4587 and
`gbrain-pg` PID 4594 retain their pre-cutover process start ticks. Docker reports
both containers started August 26; a read-only query confirms Volundr's PostgreSQL
postmaster start at August 26 23:47:27.744 UTC and data directory
`/var/lib/postgresql/data`. The same Docker volume and gbrain bind data paths
remain attached. No database/container restart or data reset was requested.

Several transient PostgreSQL processes disappeared, including PIDs 2628576 and
2628584 highlighted by the parent. They were **not** either postmaster. Their
start times coincide with the old API start, making API client backends a strong
inference; the saved pre-cutover snapshot lacks their backend titles, so the exact
child role is not established. Do not claim every PostgreSQL PID survived or infer
full data durability merely from server uptime.

**TLS correction:** the original probes sent plain HTTP to port 9501, a TLS
listener. Their errors do not show an outage. Trusted
`https://thor.tail737f2a.ts.net:9501/health` is healthy; the parent also verified it.
Agent HTTP 9500 and preview HTTPS 5300 stayed healthy; preview body digest is
unchanged. No independent service, TLS setting, database, Mini or release process
was changed. The initial 9500 full-body hash comparison was also inappropriate
because health includes uptime and live connection counts. It falsely failed; the
shell wrapper lacked fail-fast sequencing and still entered the Thor guard script.
The internal Thor gateway/unit guards remained active and triggered rollback.
Future orchestration must fail closed on command failures and compare stable
health fields, not dynamic uptime hashes.

## History: established boundary and uncertainty

- All **629** archived-inclusive Thor session identities and stable metadata remain
  equal. The initial `?archived=true` snapshot was only 63 rows: the real parameter
  is `include_archived=true`. That first snapshot is not a full archive inventory.
- For the 63 ordinary Thor and 9 Spark sessions sampled before rollout, original
  log-first frames are identical and head sequence numbers did not decrease.
  This is a prefix/head check, **not** every-event equality.
- Share with Voice's 20 turns and selected completed runner turns match the saved
  pre-cutover snapshot. Parent additionally confirmed Dictation's 96 archived turns.
- Spark's two stopped-session samples retain exactly 4 and 2 turns.
- The three affected Forge conversations and native files remain readable.
  There was no complete pre-cutover in-memory/native-buffer snapshot, so unsaved
  text, controls or in-flight tool state cannot be certified. A native command
  can have acted before its result was persisted: **never blindly resend**.
- Nine UX event-log sequence-conflict warnings were captured: five before Thor
  restart, one on the candidate, three after rollback. This is not newly
  introduced by candidate source alone; the stored original was retained. It must not be reclassified as successful complete
  streaming preservation without comparison of the competing producer frame.

## Recovery and next rollout requirements

1. Keep Thor on rollback source; do not reuse the expired maintenance window.
2. Preserve original state, logs, transcripts, archives and workspace content.
   Do not change stored session status to make an invariant pass.
3. Through the coordinator, identify whether owners want the three already-stopped
   records left stopped or explicitly resumed. Compare latest native transcript,
   Forge replay and working-tree state first. An owner-authorized resume must use
   the same native identity and workspace, without re-sending uncertain prompts
   or replaying stale approval responses. No automatic owner restart is performed.
4. Validate the isolated backend repair at the actual composition/startup boundary,
   not only PID survival or a stub PodManager. Keep Kubernetes cleanup tests intact.
5. Before any newly authorized window, enumerate **all live gateway processes and
   stored statuses**, native/tmux children, postmaster identities and known buffered
   delivery/control state. Freeze the complete candidate source/env, test it, and
   announce a new exact window. A running process attached to a stopped DB row is
   a preservation input, not permission to kill it.
6. Keep API deployment and per-session runtime adoption separate. Existing healthy
   gateways stay on old source until their owners authorize a saved-boundary
   transition. Spark native proof used its configured Codex **0.153.4**; Thor used
   **0.154.0**. The shell-default Spark binary (0.124.0) was not the tested runtime.

Evidence directory: `.local/codex-alignment-20260913/` in the isolated runner
checkout. Curated hashes and validation summaries are linked from the [rollout
evidence JSON](codex-rollout-evidence-20260913.json). Raw private histories and journal data are not committed.
