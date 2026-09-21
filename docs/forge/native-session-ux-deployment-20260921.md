# Thor native-session UX API release

September 21, 2026. **Thor deployed; iOS and macOS 2.0 (2275) subsequently delivered.**
Worker record, not user acceptance.

The user explicitly authorized the Thor API update with a brief reconnect window.
The announced window was 03:01–03:16 UTC; guarded apply ran 03:02:17–03:02:40 UTC.
Candidate `4d93cba69275534147f9b80f65c94e8a53c89cfc`, Python source digest
`40f7520e43bce65dbe3ae3671aefa496cb2cdb8cf9f915ac8b12688e1f457d82`, is deployed in
`/home/thor/.local/share/niuu/releases/forge-native-session-ux-20260921-v4`.

## Preserved and checked

- Integrated onto actual deployed `ea803c53`, retaining newer guild/proxy fixes,
  safe local startup and append/import row-lock serialization.
- All **47 protected process identities, 16 live gateways and 676 archived-inclusive
  records** match before/after in an independent readback. Two inactive conversation
  hashes and the retained last-20-sequence prefix of all 16 local gateway logs match;
  durable heads do not decrease. This does not certify every unflushed memory buffer.
- Only the single owned API source-selection drop-in was added. Existing units,
  launcher/config, database postmaster, independent agent HTTP/TLS and web preview
  checks remain unchanged. No gateway/native CLI upgrade, stop/start, prompt resend,
  archive deletion, other-host update or unrelated service restart occurred.
- Startup migration 67 is recorded with its exact SQL checksum. Private schema and
  affected-table backups were saved; not a backup of the full 50 GB event ledger.
  The prior frozen release remains the tested, compatible rollback; not used.
- Local-only normal list, archived list and SSE headers acknowledge `local`.
  At 03:04 UTC: 12 non-archived and 632 archived local records; normal-list sample
  0.029 s. Normal local rows match the explicit embedded-owner selection and contain
  no archive rows. Web guild aggregation remains unchanged. Read-state GET shape
  passed without modifying any user's read markers.

## Validation and limits

6,803 broad mocked tests pass, zero warnings; all 11 modified Python modules have
87.40% statement/branch coverage (unchanged 85% gate). Supported-target CI passes:
5,886 unit tests on Python 3.12 with the unchanged 85% Skuld coverage gate, 49 real
PostgreSQL boundary tests with no skips, tmux and browser lanes. Overall workflow
is **not green**: its obsolete Python 3.11 matrix cannot install this repo's unchanged
Python >=3.12 requirement. It was not silently removed or reported as passing.

The final corpus projection review allows only newly added `final_output: true`
metadata. Removing precisely that additive field reproduces every prior expected
turn; original captures, text, IDs, ordering and review provenance remain unchanged.
No provider calls or new live acceptance were used to refresh those expectations.

Four real-process candidate restart/reconnect cases and four safe-rollback cases
pass. Final v4 production Python/fixture/release-driver hashes are identical to the
executed v3 proof (only reviewed fixture expectations changed). Native app UI,
physical-device and live fleet stream event replay remain separate acceptance checks.

Already-running brokers intentionally retain old loaded code; automatic final-output
markers require newly loaded writers. No historical backfill marks old sessions
unread. Spark and other nodes are not updated here. Native per-host Server/guild
selection provides an explicit older-server option; local mode never silently
accepts a server that ignores its scope query.

Private evidence: `.local/release-20260921/` in
`/home/thor/repos/worktrees/niuu-native-session-ux-release-20260921`:
`apply-window1.json`, `independent-readback-window1.json`, `ledger-*-window1.json`,
`live-local-scope-readback.json`, `migration-live-readback.txt`, CI artifacts,
`restart-proof-reuse-v4.json`, `health-observation-summary.json` and backups.
Original failed/unlaunched preparation evidence is retained, not counted green.

## Paired native delivery follow-up

At 03:21 UTC (iOS) and 03:30 UTC (Mac), independent Apple reads verify the exact
uploaded build2275, VALID, IN_BETA_TESTING, devs access and matching release notes.
Native signed product is `6a49b6b508e1b4748edac16472477d34d0d8cee0`; documentation
checkpoint `6f04885a` is separate from that artifact and from the deployed backend.
No additional server mutation occurred for publication. Full report:
`/home/thor/repos/worktrees/lexi-native-session-ux-release-20260921/apps/chat/docs/session-ux-2275/STATUS.md`.
Manual UI/cross-device acceptance and other-host updates remain open.
