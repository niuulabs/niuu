# Spark live deduplication and filesystem replay follow-up

Status: server filesystem replay correction **deployed to Spark API only at
10:02 UTC**. Thor remains untouched. Native
implementation still requires a dedicated owner. This is not a claim that the
reported iOS display bug is fixed. No native files, gateway/native processes, provider turns, or saved production
history were modified. Only the Spark API source selection and API process changed.

## Observations, not inferred device reproduction

The user reports improved steering/timestamps but duplicate live actions and stale
running cards after an input triggers polling. Settings/About was described as
“latest”; exact installed-device build remains unverified. Native source reviewed
at delivered **2.0 (2263), f9b78bd1**.

Fresh test: `spark:0cf947a5-84d1-5a59-83ad-3f5b93752c17`,
`lexi-spark-codex-steering-test`. Parent verified the actual new gateway on clean
`39da0146a2957f81f51bad54c107d7b042e55cd2`, `timeline-v3`, not merely the API.

Read-only live capture at approximately 09:41 UTC contains:

- 12 display rows, 15 identified text items, 46 tool calls and 46 tool results.
- Zero duplicate row IDs, scoped text identities, call IDs or result IDs.
- Every captured call has a matching result and `ended_at`.
- One `in_progress` display row, not two native executions.
- Four assistant fragments share active `span_seq: 812`, with three steering
  inputs between them. One fragment is **tool-only**: three calls and results,
  no text. This is important to the native matching failure below.

Raw start/delta/stop/full frames legitimately repeat item identities as they
update an item. A tool call and its result also intentionally share their pairing
identity. Neither is a reason to discard the newer completion payload.

The test was already archived at **09:42:37.977238 UTC** before follow-up GETs.
This worker did not archive it. Its absent gateway returned HTTP 404; this was a
read, not a gateway failure/restart caused by the investigation.

## A separate, reproduced server omission

The subsequent archive conversation returned **9 rows**, not 12, with all six
assistant display IDs missing and three cumulative assistant rows instead.
Native items, timestamps and tool results were retained. The response still
advertised `text-items-1:0;timeline-1`.

`volundr.session_archive._normalise_transcript_payload` computed the projection
revision but did **not** apply the timeline projection. The workspace/archive
filesystem path bypassed both the live broker projection and the already-fixed
durable event-log reducer. Prior reducer/cache tests did not cover this actual
filesystem archive boundary. The earlier blanket live/archive identity claim was
therefore too broad.

Applying the existing shared projector **offline** to the archived response
restores all 12 live row IDs, their order and dates exactly. All 107 previously
captured parts retain their identity and complete payload, with one newly emitted
final text item added before archival. No source mutation or database backfill is
required. This is offline evidence, not evidence that Spark has the correction.

The isolated correction passes the session identity into workspace/archive read
normalization and applies the same projection before writing new JSON/Markdown
archive artifacts and manifest counts. Reads do not rewrite existing files;
untimed legacy payloads remain unchanged. Anonymous legacy archive callers do not
invent a session identity. Existing downloadable artifact files are not silently
regenerated; their immutable bytes are outside this read-projection correction.

Regression fixture: `tests/fixtures/codex_steering_archive_timeline.json`.
It is entirely synthetic, not copied user/tool content. Four workspace/config
archive read/write regressions fail on the previous source and pass after the fix.

## Native defect paths requiring implementation

All source references below are at **f9b78bd1**, in
`apps/chat/LexiChat/V2/Views/CodeSessionLiveModel.swift` unless noted.

1. **Freezing separates a call from its future completion.**
   `freezeVisibleLiveFragmentForSteer` (3790) copies the builder into `history` and
   resets it. `ingestMessage` (3132) sends a later result only to the new builder;
   `LiveTurnBuilder.applyToolResult` (4986) creates an unmatched result if the call
   is no longer there. The old frozen copy is not directly updated. Correct
   reconciliation must pair results across history/frozen/live storage by tool
   identity, updating the original card rather than creating another flow.
2. **The legacy prefix heuristic can insert a duplicate tool-only fragment.**
   `canonicalTurnsPreservingSteeringOrder` (1847) seeks a canonical assistant by
   row ID or the first 80 text characters. A textless local fragment without a
   matching server row ID cannot match, even when every tool ID exists in the
   canonical response. Its not-found branch inserts that stale local fragment
   beside the authoritative one. The later guard at 1764 skips locals with no
   identified text items, so tool-only duplication is not caught there.
3. **One native turn is not one display row.** The same guard and
   `applyCanonicalLiveSeed` (1538) compare one canonical fragment to a potentially
   cumulative local builder, matching even on shared native turn/thread alone.
   One fragment cannot cover all local items distributed across several canonical
   rows. This can reject a legitimate refresh or preserve already-covered output.
4. **Active fragments are not an immutable cache prefix.**
   `ForgeSessionDiskCache.settledPrefixCount` (151) stops at `isInProgress`, pending
   input, or a few local flags; it does not recognize earlier server fragments of
   the same active `span_seq`. Those rows lack top-level `in_progress` but can
   still receive a late result/text completion. `pollConversationFetch` (1088)
   can advance `after` past those items. `writeBackDiskCache` (1121) only rewrites
   on span growth, so an equal-sized completion correction can remain stale on
   disk. A matching `after_id` proves row position, **not content immutability**.
5. `user_active` (2806) launches an immediate reconciliation alongside periodic
   polling. Verify out-of-order fetch completion: an older same-revision snapshot
   must not regress a newer accepted state. This is a race-test requirement, not
   yet a measured device-level cause.

These are source-level paths consistent with the report. No Swift execution,
simulator rendering, physical-phone trace or exact device-state capture was
performed here. Do not label a static review as a rendered reproduction.

## Required reconciliation behavior

- Use authoritative ordered display row IDs, with an item-membership index over
  **all** fetched canonical fragments and existing local/history/live items.
  Scope text by native thread/turn/item and tools by session/native context plus
  call ID. Never use command text, response text, row index, timestamp or native
  turn ID alone as a uniqueness key. Two equal-text user requests remain distinct.
- Upsert repeated identities. Merge completion/status, exact input, output, phase
  and timing into the original item. One call card per identity. A late result
  updates that card even if steering has moved the live builder elsewhere.
- Remove only covered items from frozen/live overlays. Retain genuinely newer
  live-only items, including mixed covered/uncovered fragments; drop empty local
  rows. Do not wait until the entire session goes idle to retire covered activity.
- Do not run the legacy cumulative-prefix splitting heuristic over an already
  timeline-projected row. Preserve a separately tested path for untimed/old servers
  and explicitly uncertain offline steering. Never hide genuine uncertainty.
- Keep the poll/cache seam before the earliest potentially mutable fragment of an
  active native span (`conversation_timeline.span_seq`), including tool-only rows.
  Fetch that suffix through terminal settlement. Persist content corrections even
  without a row-count increase; do not cache frozen/local rows as absolute server
  indices. If the active span begins before the loaded window, do not pretend the
  visible suffix proves an immutable prefix.
- Respect snapshot generation/evidence guards; never erase newer socket data with
  a stale fetch. Keep the full-output cache when a shallow response elides content.
  Deduplication must merge evidence, not simply “ignore any ID seen before”.
- Seam mismatch or repair revision invalidates the affected cached projection;
  reconciliation after refetch must still be idempotent by identity. Preserve real
  steering, no-resend recovery, provenance, permissions and both harness families.

## Shared synthetic acceptance scenario

Use the fixture above as cumulative archive input, with the shared projector's
expected canonical order: initial user, tool-only assistant, steer one, commentary,
steer two, final text. Both steers intentionally say “Continue” with distinct IDs.

For native tests, first deliver the call without its result, freeze it for steer
one, deliver commentary, then freeze for steer two. Deliver the tool result later
and reconcile the same canonical full/shallow response repeatedly, including
out-of-order REST responses and overlapping windows. Assert by item identity:

- exactly one tool card and one copy of each identified text item;
- original tool card completes, exact command/output preserved;
- two distinct steering rows, authoritative dates and order;
- no covered frozen duplicate, lost live-only suffix or permanent spinner;
- stable state through result, background/reconnect, close/reopen, process relaunch
  and archive-source transition; no message resend;
- cached earlier fragment receives late completion even when row count is stable.

The fixture and server tests do not replace these native tests or user acceptance.

## Ownership and evidence

Runtime worker: `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`.
Coordinator: `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
Prior native handoff `d40c6f9e-edbf-4aab-9bd6-a19ebc4edc57` remains outstanding.
New diagnosis/escalation receipt `a80f1907-4da7-4ac6-b000-2d7508ba9574` explicitly
asks the coordinator to assign this native work; it is not implicitly assigned to
the concurrent chat-UX worker. OpenClaw milestone delivery: message 6681.

Private capture, hashes, red/green logs and source audit:
`.local/codex-live-dedup-20260914/` in the runtime checkout.
See `capture-summary.json` and `offline-archive-projection-summary.json`.
Keep conversation and command contents private. Thor remains held; any Spark
API-only cutover requires fresh owner-preservation evidence and an announced window.

## Validation checkpoint before rollout

Candidate source: `650088f3` (fixed snapshot; only filesystem archive projection
and regression tests differ from the previous product source). Thor-owned suite:
5,128 passed, 24 skipped, 95 deselected, one expected failure; 85.77% scoped
statement/branch coverage, with the unchanged 85% gate. Four additional REST
filesystem regressions pass separately (added after the sweep collected its
tests). The earlier focused archive/timeline suite passed 86 tests. These counts
overlap; do not add focused tests to the full-suite total. New helper lines are
covered. Source-path audit, Ruff checks and commit hooks pass.

The REST regressions exercise the real archive service and filesystem adapters
through full/shallow responses, repeated `after`/`after_id`, a mismatched seam,
recent windows and full tool-result retrieval. No provider or production database
is involved. Two captured legacy archived transcripts remain byte-equivalent at
the turn-payload level under the new read projection.

## Spark rollout and independent readback

Announced window: **10:02–10:12 UTC**; window receipt
`cebeafbc-5f4a-4edb-ac63-a8b2dffcff89`, OpenClaw message 6683.
Actual guarded cutover: **10:02:51.917277–10:02:56.833643 UTC**.
Candidate `650088f3e2fe234e75cd7d2578acae911290ee91`,
source SHA256 `57446280f7a8bc81d6ae0b8215f8e8df528f0d28add876ac272bd39d3ebff000`,
build `forge-codex-ready-20260914-timeline-v4`.
Exact staged Spark suite: **5,132 passed**, 24 skipped, 95 deselected, one expected
failure; **85.77%** scoped statement/branch coverage, unchanged 85% gate. Both
source-root and host-local dependency/composition audits pass.

Host release root:
`/home/xteo/.local/share/niuu/releases/forge-codex-ready-20260914-timeline-v4`.
Only `niuu-forge.service` restarted, with one new owned source-only drop-in:
`~/.config/systemd/user/niuu-forge.service.d/zzzzzzzz-steering-timeline-v4-20260914.conf`.
Safe rollback remains `39da0146` / timeline-v3, already containing the local-process
preservation fix. Existing drop-ins/configuration/auth/mounts remain intact.

Guard and independent follow-up verify:

- All **14 session identities/statuses** retained (5 archived, 9 stopped).
- **Four protected process identities** unchanged: three PostgreSQL postmasters
  and tmux PID 5640, including PID/start ticks/boot ID/command hashes.
- No live Spark gateways existed at cutover; therefore this rollout makes no new
  empirical active-turn reconnection claim. The real-process isolated preservation
  tests still pass; no owner was resumed, resent to, or restarted.
- All **13 unrelated session turn payloads** unchanged, including selected archived
  samples. The newly timed test alone intentionally changes derived display order:
  **9 cumulative rows → 12 rows**, matching its live row IDs/order/dates and the
  precomputed projected-payload hash. Fleet display row total **59 → 62** is this
  correction, not new messages or database inserts.
- All **2,357 public test log entries** retain exact hashed contents.
- Source health clean; saved unit/config bytes and separate 9500 HTTP, 9501 trusted
  HTTPS and 5300 HTTPS preview checks pass. No forced-stop/failure indicator found
  in the scoped API journal. Thor was not redeployed.

The corrected API can now replay the archived test with its live display identities.
This is **not** acceptance of native live deduplication: frozen/tool-result merging,
mutable cache boundaries and rendered/relaunch tests remain the dedicated native
assignment. Existing downloadable artifact bytes are preserved, not retroactively
rewritten. Detailed sanitized evidence: [JSON](codex-live-dedup-evidence-20260914.json).

Deployed milestone OpenClaw delivery: message 6685. Notification delivery is not
feature acceptance or proof of a native-owner assignment.
