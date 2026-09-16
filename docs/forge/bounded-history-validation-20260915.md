# Bounded history — server candidate validation

September 15, 2026. **Isolated candidate; not deployed or accepted for release.**
Thor/Forge/network HOLD remains. No gateway, owner session, shared service or
native runtime was restarted, adopted, modified or sent test input.

## Candidate

- Source pin: `b99449cc51a7fb381fc7fc71fe43f388a3f43580`.
- Branch: `xteo/forge-runtime-management-20260913`.
- Changes follow `64b95f67dea0830ee809faf6fad1139b0c8e8f13` in the dedicated
  `/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913` checkout.
- [Wire contract and remaining limits](bounded-history-protocol-20260914.md).
- [Machine-readable validation and private evidence checksums](bounded-history-validation-20260915.json).

## What is verified

The selected server regression sweep passed **5,378 tests**, with 24 skipped,
95 deselected and one expected failure. Warnings were errors; none occurred.
The unchanged 85% gate passed at **85.14%** with branch coverage enabled over the
17 modules explicitly listed in the saved coverage configuration. This is not a
claim of whole-monorepo coverage or live/physical-device acceptance. Integration,
broker, kind-integration and live-CLI markers were excluded. Eighteen imported
modules were independently checked to come from this owned checkout, not the
inherited live-server `PYTHONPATH`. Final pytest/process exit status was zero.
Ruff lint and format checks passed for all 26 changed Python files.

An offline replay of the affected session's previously captured 63 rows produced
five bounded pages of 13, 15, 12, 12 and 11 rows. Wire sizes were 222,614, 246,031,
180,745, 238,489 and 147,465 bytes: all below 256 KiB. Concatenating older pages
restored all 63 distinct identities in their exact original order. The captured
source was unchanged. This was not a fresh connection to the active session.

Focused tests cover source-side bounded serialization, API adaptation of retained
gateways and archives, append-stable older cursors, fixed refresh continuations,
explicit huge-item references, metadata identity/privacy, authorization, typed
stale/busy errors, snapshot/live races, cancellation and overflow. Exact-code
recovery controls do not become assistant messages; genuine error text is retained.
Sender-only ingress keeps its original request identity and ACK behavior.

Transport tests found and fixed an additional cancellation leak: cancelling the
outer WebSocket bridge left its two forwarding tasks alive. Both pumps now drain
on cancellation or either transport ending. In-memory bidirectional tests preserve
one exact steering payload and one exact recovery control without duplication.
This is not evidence that the leak caused the user's historical display defect.

Earlier sweeps are retained, not relabeled successful: the initial sweep exposed
six functional failures and mock warnings, subsequently fixed; later all-functional
passes missed coverage at 84.59% and 84.90%. Relevant root-proxy and cancellation
tests brought the same expanded coverage scope above the unchanged gate.

## Native handoff

Fixture: `tests/fixtures/history_protocol_v2.json`, SHA-256
`2dc0cf3292d34adefa4f4971deceb7cbfa8cb8a5b244502835f721d6494f8338`.
Initial, older and all four refresh examples are unchanged from `2712ba28`.
The appended metadata-preview case regenerates exactly from the server code and
is 855 wire bytes. It retains `history_metadata_preview`, row-level `history_ref`,
original role/ID/time, internal visibility and participant identity. The full-item
route remains separate from automatic paging and recovery.

The native owner reports Foundation decoder/join/continuation roundtrip success
and neutral loadable metadata previews. That report is not Apple model/UI/visual
validation; those gates belong to the native owner. No native source was imported.

## Boundaries that remain

This candidate fixes transport/replay presentation, not every storage cost. An
already-running old gateway still builds its full upstream REST response. The new
gateway still scans full in-memory timeline/identity metadata. Archive JSON reads
still load a projection, and database-only recovery still has the existing newest
50,000-frame ceiling: totals may describe an incomplete historical projection.
Persistent DB-native turn paging and complete older backfill remain unresolved.
Do not treat the new presentation cursors as proof that these storage limits are
fixed. No database schema or raw ledger was rewritten.

Next: coordinator evidence review and native contract/runtime validation. No
deployment, rollout window, or restart is authorized by this candidate handoff.
