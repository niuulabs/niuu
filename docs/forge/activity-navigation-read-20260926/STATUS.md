# Individual activity reads — deployed; retained-gateway limits noted

September26,2026. After approving the per-activity API, the user requested deployment.
Thor Forge4b44432f and migration000071 are live; iOS2.0(2291) is on TestFlight.
Existing gateways/native sessions were preserved, so some older reply aliases remain
unavailable until a safe runtime upgrade. Deployment and live acceptance are below.

The following local validation preceded that separately authorized rollout.

## Behaviour

- `PUT /api/v1/forge/notifications/{id}/read?instance_id=<host>` acknowledges exactly
  one visible item for the current reader. Idempotent receipt, no watermark advance,
  no cross-host fan-out, no other reader affected, missing/invisible item404.
- Item marks and mark-all share the reader revision/row lock. Repeated acknowledgements
  do not increment revision; a concurrent item read invalidates an old mark-all CAS.
  Feed and session read flags, unread filters/counts, and Guild revision summaries agree.
- Migration000071 is additive and mirrored in local SQL, packaged CLI migrations,
  and Helm configmap. It cascades only a deleted notification's individual marks.
- Projection fragments retain durable source-turn provenance. Exact IDs win; otherwise
  only a unique terminal fragment can resolve a source ID. The response carries the
  requested ID and the resolved row's absolute index. No title/time/log-seq guessing.
- Earlier compact MCP authoring guidance113acac1 remains included.

## Validation

[266 focused tests](TESTS.txt),98.57% branch/statement coverage across the six focused
notification/projection/policy modules;85% gate retained. All modified REST/gateway
paths are exercised via HTTP/mocked infrastructure, including archive and live gateway
anchor windows. [243 replay/archive/MCP regressions](REGRESSIONS.txt) and
[9 migration parity/DDL checks](MIGRATIONS.txt) pass. Zero reported warnings; Ruff
check/format and `git diff --check` pass.

[Wheel source and packaged-migration inspection](BUILD.json) passes. PostgreSQL SQL
is checked against mocked asyncpg as required; no database was started or mutated.
The opt-in native PostgreSQL tests now include concurrent individual acknowledgements,
reader isolation, CAS and cascade/reversibility. They were **not run**: a disposable
CI database is required. Full repository CI is not represented as passing.

## Rollout boundary

This is **not deployed**. Recheck migration numbering against the integration target
before any authorized rollout. Update both Forge and the owning Skuld gateway for
source-turn aliases. Already-running older gateways and projected-only archives
without source provenance can still return404. Do not restart active sessions just
to refresh code; arrange an authorized recovery window. A missing anchor is explicit,
never falsely reported as the linked message. Item-read capability is independent of
whether that session's old gateway supports reply aliases.

Source branch `xteo/activity-navigation-read-20260926`, isolated worktree
`/home/thor/repos/worktrees/niuu-activity-navigation-read-20260926`.
Worker `thor:c8103dc7-a834-5e32-a06d-1a98d7a8e2b5`. Native app follow-up lives in
`/home/thor/repos/worktrees/lexi-ios-activities-20260926` and remains unshipped.

## Deployment — September26,22:10UTC

The user subsequently requested deployment. Frozen source4b44432f is live on Thor as
`forge-activity-read-20260926`, with clean source identity and no failed plugins.
Migration000071 applied through the existing checksum-ledger startup runner. All80 prior
packaged ledger checksums matched; the new sequence was unused. The previous Projects
release remains the safe source-only rollback, with the new additive table retained.

[Rollout summary](deployment/rollout-summary.json):64 protected native/database/gateway
processes,21 gateway routes and710 session identities/statuses are unchanged. The first165
settled turns of the worker conversation match exactly. Existing units/config/launcher
are byte-identical; the healthcheck timer is active.39 extra release-guard/composition/
real-process preservation tests pass. Private full PG backup completed; its408-entry TOC
and SHA were checked, not a full restore. The first TOC command lacked Docker stdin and
failed before reading; the corrected read succeeded. No backup data is checked into Git.

Backend branch is pushed; normal web build/typecheck/test coverage/format pre-push gates
passed after installing the frozen dependencies in this worktree. No hook bypass.

**Retained runtime limitation:** the API-only cutover deliberately did not restart active
Skuld/native sessions. Existing old gateways can still return404 for original Codex reply
IDs without source provenance. Canonical notification links resolve live; old aliases need
an owner-approved saved-boundary gateway upgrade (or separately reviewed compatibility
work). No guessed text/time anchor or claim that old gateway source changed.

Live successful read-on-open is being validated through iOS2291 before publication.

Live read acceptance now passes through the actual iOS2291 simulator: two unread own QA
cards, opening one produces exactly one item mark, unread2→1 and unchanged watermark72.
The older card stays unread; repeating the item PUT keeps revision5. Another client later
explicitly advanced the shared watermark to74; that distinct Mark all action is retained
and not attributed to the app item endpoint. The item mark persists through app relaunch.
[Live evidence](deployment/live-read-acceptance.json). No new gateway/provider session
was created. iOS release evidence is in the app's2291 report.
