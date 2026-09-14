# Codex steering chronology — September 14 follow-up

## Release status at the compatibility correction

The first Spark candidate started healthy at 08:20 UTC but **was rolled back**
by the archived-replay equality guard. Its global cross-seed ACK fold had added
`steering_state: pending` to an old saved row with empty metadata and changed
another old pending label to active. These were metadata-only read-projection
changes, not lost messages or native-process deaths. The automatic safe rollback
completed at 08:20:31 UTC. All six protected owners, 13 sessions, 50 complete turn
payloads and 258 captured test log rows were verified preserved afterward.

This revision limits cross-seed lifecycle reconciliation and the adjusted seed
cutoff to explicitly timed new-protocol input. Untimed authoritative history keeps
its old projection and raw-tail semantics exactly. It does not relax the guard.
Three captured Spark histories now match the previous release exactly through
raw reduction, raw rebuild and authoritative canonical-seed reconstruction. The
latter uses synthetic seed positions around actual captured canonical content,
not a claim of captured internal database rows.

The corrected owned-checkout sweep passes **5,084 tests**, 24 skips, 95
deselections and one expected failure, with **86.00%** scoped statement/branch
coverage. The initial source below is not the accepted release. A replacement
immutable staging audit, fresh preparation and a newly announced window are
required before another attempt; no retry is authorized by the expired attempt.

## Diagnosis

The user's fresh Spark test confirmed the native delivery improvement: both
steering requests used the same executing Codex turn without interruption. The
remaining defects straddle the server's presentation model and the native merge.

The server had dates for both inputs, but omitted them from live confirmations.
It also exposed one cumulative assistant row after both inputs, combining all
pre/between/post-steer activity. The native client additionally retains frozen
local fragments during reconciliation; source inspection identifies a whole-row
match by native turn ID and a text-prefix heuristic that are incompatible with
multiple canonical display fragments in one native turn. The reported temporary
duplicates were not found as duplicate stored rows.

## Implemented server correction

Initial candidate `273e9aa328521ba79944b79866c7b4d09ed8ce34`, Python source digest
`3e165f1528102e804a829eb7287b31d615f73ee9fe3fcd2e6e7c383f1fbb41f6`.

- One input insertion observation supplies canonical and echo timestamps; native
  acceptance observation remains a separate, honestly named field.
- Durable live-steering frames and their native items carry first-observation
  positions. Shared chronological projection splits only display rows, not the
  native execution or text/tool items.
- Late text/tool completions update the original item and slot, never duplicate
  a prefix or issue the command again. Tool results remain with their call.
- Fragment IDs, ordered parts and input dates agree across live REST, WebSocket
  reconnect, saved cache/reload and raw/seeded database-log reconstruction.
- A later timed user seed no longer hides an unflushed assistant prefix after an earlier
  completed turn. Replay applies logged input-delivery outcomes even when the
  raw lifecycle frames precede a later saved assistant seed, for explicitly timed input.
- Recent WebSocket history is projected before windowing without deep-copying
  heavy tool payloads outside the requested window.

No changes to native `turn/steer` routing, runtime provider calls, project
instruction/provenance integration, authentication, database schemas, native
source, slash workflows, Voice/Live, or shared service configuration are in this
source commit. It retains the previously integrated fixed UX input, not a moving
UX branch. The immutable event ledger is not rewritten; legacy untimed spans are
not retrospectively positioned by guesswork.

## Validation

The owned-checkout sweep passed **5,082 tests**, with 24 skips, 95 deselections and
one expected failure. The selected production files have **85.96% statement plus
branch coverage** against the unchanged 85% gate; the new pure chronology module
has **100%**. Ruff lint/format and diff whitespace checks passed. The source audit
verifies imports from this runner's checkout with Python 3.13.13.

Earlier failing runs are retained privately: exact metadata fixtures needed the
new fields, a seed/lifecycle defect was fixed, and the first broad coverage run
omitted existing text-repair/import/tail suites. The final sweep includes those
suites rather than lowering the gate or excluding changed production code.

The two-steer integration fixture uses the real broker and Codex adapter with a
hermetic native RPC/ledger. Full/recent WebSocket snapshots, file reload, raw and
seeded replay, item completion after steering, native-origin input, repeated
polls and a crash tail are covered. Existing local API restart-preservation and
harness compatibility tests remain in the broad sweep. These are **not** physical
iOS tests, provider calls or proof of real PostgreSQL crash durability.

An offline counterfactual using the affected Spark session's retained public log
observations produces six ordered rows instead of four while preserving every
assistant part payload (29/29), all human identities and their content. It is not
deployed output or a database backfill. Private capture, test logs, source audits
and delivery receipts live under `.local/codex-steering-replay-20260914/` in the
runtime checkout.

## Native follow-through and release boundary

The user described the client as “latest”; delivered iOS 2263 source was inspected,
not a device-confirmed build number. The [native handoff](codex-steering-timeline-ios-handoff-20260914.md)
defines the wire fields, item-membership reconciliation and rendered acceptance
matrix. A server-only release is not a claim that iOS's transient duplicates or
uncertain-position label have been fixed. A separate native owner must implement
and verify that merge, without resending prompts.

Only Spark is eligible for this iteration. Thor remains held. Existing Spark
sessions must retain their gateways/native turns; a new API version does **not**
hot-upgrade their loaded Skuld code. Candidate acceptance requires a new gateway
or a separately owner-approved saved-boundary adoption. No existing session is
automatically restarted or resumed. The original captured test history is not
rewritten. Follow the guarded [local API release procedure](local-api-release.md)
with a fresh window, inventories and safe rollback; staging alone is not deployment.
