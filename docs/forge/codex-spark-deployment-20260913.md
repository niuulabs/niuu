# Spark-only Codex / Forge deployment — September 13, 2026

## Scope and result

Direct user instruction: **“Deploy on sparks first.”** The runner announced a
new **22:00–22:15 UTC** Spark-only API window in chat, through OpenClaw, and in a
Forge receipt. Thor was observed read-only, not deployed. No shared configuration,
database, authentication, native CLI installation, session owner or iOS release
was changed. Only one source-selection systemd drop-in was added on Spark.

The guarded cutover **passed**, without rollback:

- Fresh windowed preparation: **21:58:05–21:58:07 UTC**.
- Apply, including another complete preflight: **22:00:19–22:00:39 UTC**.
- Candidate: `f5d7a5e49eafd765f8daf53bdc51c95ac1949939`.
- Python source SHA-256:
  `47dfd370523fefaabcf9189808495a942c9548d3c148203d4251ab15907c42a1`.
- Spark source: `/home/xteo/.local/share/niuu/releases/forge-codex-ready-20260913-v2`.
- API PID changed from `3415148` to `3522975`; new health is clean, with no failed
  plugins. Effective working directory, source environment and exact owned
  override agree with the immutable candidate.
- Safety-only rollback `e8c814f854beb30d00d068622d63a3c3ab244976` remains staged,
  unused. No rollback to the known-unsafe original classifier is permitted.

The frozen candidate includes the agreed Project UX input `11e9e0c`, not the
other runner's moving instruction/publication work. Improvements and deferred
slash/iOS work are in the [release notes](codex-release-notes-20260913.md).

## What was preserved

- **12 archived-inclusive Spark session records**, including stable statuses,
  source, parent/native-session/project identity and coordination metadata.
- **All 12 REST conversations / 46 turns**, compared by exact canonical turn
  hashes before and after cutover. The guarded archived samples also match.
- **Four protected process identities:** three PostgreSQL postmasters and one
  tmux server; PID/start-time/boot-ID/command fingerprints match.
- Original unit/drop-in and configuration hashes match; `KillMode=process`
  remains effective. Only `zzzzz-preservation-safe-20260913.conf` was added.
- Independent agent HTTP on 9500, trusted HTTPS on 9501, and unchanged static
  preview content over HTTPS on 5300 passed. No TLS bypass or service restart.
- Thor immediate post-cutover read-only comparison: same API PID `61138`, **629** retained session
  identities, **nine** live gateways, **30** protected process identities and
  exact 20/96-turn archived acceptance samples. Original unit/config and health
  remain unchanged. No Physics or other owner was signalled or resumed.

Delayed Spark verification at **22:01:49 UTC**, after the first configured
60-second active-session reconciliation interval, passed the same preservation
comparisons. The API remained active with `Result=success`, no automatic
restarts, and 19 models available from its configured Bifrost catalog.

Final Spark verification at **22:06:16 UTC**, over five minutes after startup,
again matched the complete protected snapshot and all 46 conversation turns.
The API PID, clean build, catalog and independent checks still passed. The
maintenance work was then closed; no further service action was scheduled.

### Thor concurrent native-process change — not a preservation pass

At **22:06:17 UTC**, the strict full Thor comparison rejected process equality:
native Codex wrapper/app-server PIDs `232702`/`232709` were gone, and new PIDs
`255524`/`255531` had the same respective command hashes. Their observed start
time was 22:02:22 UTC, parent `184156`. The earlier immediate post-cutover
comparison had retained all 30 owners. The original 28 owners, all nine gateways,
all stored identity fields, archived samples, API PID/source, unit/config and
independent checks remained equal; total protected owners was still 30.

This runner did not signal or launch those processes. Their owner, reason for
exit and relationship to each other were **not established**, so this is neither
claimed as harmless owner activity nor attributed to the Spark rollout. The
failed strict comparison and explicit drift are retained for coordinator review.
No record was rewritten or owner resumed to manufacture equality. Thor remains
undeployed and requires a fresh inventory before any future action.

## Shutdown and startup observations — not hidden by green health

The **old Spark API did not exit within its existing 15-second stop timeout**.
At 22:00:36 UTC, systemd sent SIGKILL to **that API PID only**, then started the
candidate. `KillMode=process` protected the other owners, whose identities were
verified unchanged. This was not a graceful-shutdown proof and not a gateway or
database restart. The apply elapsed time includes that wait; it is **not** an
exact measurement of the HTTP outage.

The new API logged one configured-catalog connection warning while its own HTTP
listener was starting. The same catalog loaded 19 models about 113 ms later and
again on its normal refresh. The known missing bundled web assets warning also
remains; those routes were already unavailable before the release, and the
separate HTTPS preview remains healthy. Neither warning is omitted from the raw
journal. No later runtime error or reconciliation failure was observed in the
retained verification interval.

Before considering Thor, inspect the old API shutdown timeout as an operational
limitation rather than claiming an entirely graceful release. The existing
abrupt-death/reconnect tests are relevant, but do not remove the need for fresh
live-owner preservation review on Thor.

## Proof boundaries and next owner

Spark had **zero live Skuld gateways** before and after this release. Therefore
this canary proves deployment selection, retained database-backed REST history,
protected process survival and service health, **not an active user turn crossing
a production API restart**. Earlier real-process reconnect fixtures and native
Codex provider tests remain separate evidence in the
[readiness checkpoint](codex-release-readiness-20260913.md). No provider test or
synthetic production session was launched to inflate this canary's claim.

REST turn equality is not a comparison of every event-log frame, unflushed buffer
or physical iOS reconnect. Existing gateways retain their loaded source; no
automatic adoption, owner restart, replayed prompt or replayed approval occurred.
The three older incident sessions were not resumed. **Thor remains untouched**;
this result is not an instruction to deploy it or to wake any owner.

## Evidence

Curated fingerprints and observations: [deployment evidence](codex-spark-deployment-evidence-20260913.json).
Private Spark originals are in
`/home/xteo/.local/share/niuu/spark-release-20260913-2200/`; runner copies and
Thor comparisons are in its `.local/spark-release-20260913/`.

The maintenance notice is receipt `2527aaf0-1270-4230-8877-e7e1895bd774`;
OpenClaw message `6660` returned `payload.ok=true`, not user-read/acceptance proof.
Completion notification `6662` also returned `payload.ok=true`, with the forced
API stop and no-live-gateway limitation stated explicitly.
Preparation, manifest, apply, source proof, conversations and journals have
separate immutable filenames. Prior incident evidence remains intact.
