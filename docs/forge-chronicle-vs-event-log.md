# Forge chronicle vs. session event log

Status: normative for FR-10 of the
[Forge persistence-unification SRD](./forge-persistence-unification-srd.md)
(see §3.7 "A parallel, lossy, Claude-only timeline exists" and §6 FR-10
"Chronicle as derived").

Forge has **two** stores that both look like "a record of what happened in a
session". They are not equals. This doc states which is canonical, why they
differ, and how a consumer must treat each.

## TL;DR

| | `session_event_log` | chronicle timeline |
|---|---|---|
| Role | **Source of truth** for the transcript | **Derived** UI aggregate |
| Authority | Canonical, authoritative | Non-authoritative |
| Completeness | Complete superset of every live frame | Lossy (curated UI subset) |
| Transport coverage | All transports (Claude / Codex / Grok / OpenCode / tmux) | Claude CLI only |
| For non-Claude sessions | Fully populated | **Legitimately empty** |
| Producer | Broker appends every frame, in order, with monotonic `seq` | `chronicle_watcher` tails on-disk `.jsonl`, `event_mapper` summarises |
| Consumed by transcript? | Yes — folded by the shared reducer | **Never** |

If you need the transcript, read `session_event_log` and fold it with the
reducer. Never read the chronicle for that purpose.

## What each store is

### `session_event_log` (canonical)

The durable, append-only, per-session event log. The broker writes **every**
event that any client can ever see to this log first, in order, exactly once,
with a monotonic per-session `seq`. It is a *complete superset* of every live
broadcast (SRD §4, objective 1).

The canonical transcript is produced by folding these log frames with the
single shared reducer:

- `src/niuu/domain/transcript_reducer.py` — `reduce_frames(...)` (the pure
  fold; the SINGLE folding contract, INV-4).
- `src/volundr/domain/services/transcript_rebuild.py` — `rebuild_turns(...)`
  (the rebuild path that drives the reducer over the durable log).

This fold takes **only** `session_event_log` entries. It has no input from the
chronicle and never reconciles against it.

### The chronicle timeline (derived)

A separate pipeline, Claude-CLI-only:

- `src/skuld/chronicle_watcher.py` — tails Claude Code's on-disk `.jsonl`
  session files (inotify, with a polling fallback) and POSTs summarised events
  to `/api/v1/forge/chronicles/{id}/timeline`.
- `src/skuld/event_mapper.py` — `EventMapper`, which keys on Anthropic on-disk
  JSONL shapes and maps a small curated set of events (file changes, git
  commits, terminal commands, token usage) onto the timeline.

This is a UI convenience view ("what notable things happened, at a glance"),
not a record of the conversation.

## Why they differ

1. **Different source.** The chronicle reads Claude's on-disk `.jsonl`. The
   event log is written by the broker from the live CLI event stream. They are
   tailed independently, so the chronicle can lag or drift.
2. **Claude-only shapes.** `EventMapper` pattern-matches Anthropic JSONL.
   Codex, Grok, and OpenCode do not write that file, so nothing is mapped and
   the chronicle is **legitimately empty** for those transports. The event log
   is populated for all of them. An empty chronicle is therefore not a sign of
   data loss.
3. **Lossy by design.** The chronicle deliberately keeps only a curated subset
   of events; the event log keeps everything.

## FR-10 disposition (the decision)

The chronicle is an **explicitly derived, non-authoritative UI aggregate.**
`session_event_log` is the single source of truth for the transcript.

Concretely:

- We do **not** make the chronicle a second source of truth.
- The canonical transcript (durable log -> reduce / `get_transcript`) must
  **never** depend on the chronicle. This is pinned by a boundary test
  (`tests/test_skuld/test_chronicle_is_derived.py`): the reducer rebuilds the
  full transcript from `session_event_log` frames alone, and an empty/absent
  chronicle does not change or reduce that transcript.
- Consumers must not treat the chronicle as the transcript.

## Future option (NOT done in this epic)

The chronicle could be **re-derived from the canonical stream** instead of from
on-disk `.jsonl` — i.e. fed from the same `_handle_cli_event` frames the broker
already appends to `session_event_log`. That would make the chronicle cover
every transport (not just Claude) and remove the drift, while keeping it a
derived view. It is deferred here because it would touch `src/skuld/broker.py`,
which is out of scope for this epic. Until then, the disposition above (derived,
non-authoritative, Claude-only, may be empty) is the contract.
