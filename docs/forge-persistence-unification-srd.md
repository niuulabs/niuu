# SRD — Forge Session Persistence & Live/Database Unification

| | |
|---|---|
| **Status** | Draft → In implementation |
| **Owner** | Forge / Volundr platform (lead architect: API-consolidation effort) |
| **Branch** | `lexi/api-consolidation` (off `lexi/dev-api-integration`) |
| **Supersedes** | ad-hoc divergence between live broker stream and durable `session_event_log` |
| **Related** | `docs/forge-session-state-reporting.md`, `docs/openclaw-session-orchestrator-guide.md`, `docs/testing/skuld-tmux-comprehensive-test-plan.md` |

---

## 1. Purpose

Forge runs agent coding sessions across multiple state-of-the-art CLIs (Claude SDK/tmux, Codex, Grok ACP, OpenCode). A client interacts with a session in one of two ways:

- **LIVE** — a full-duplex WebSocket to the per-session Skuld broker (can send/steer).
- **HISTORICAL / DATABASE** — read-only views derived from the durable `session_event_log` (REST cold-read, paced replay-WS, crash-rebuild).

Today these two worlds can disagree. The goal of this SRD is to make the **durable event log the single canonical, ordered, complete record of everything that happens in a session**, and to make every other view — the live broadcast, the replay, the cold read, the post-crash rebuild — a *projection of that one log through one shared reducer*. When this is done, "what you saw live" is provably identical to "what you replay later," at any time, across connect / disconnect / reconnect / crash / resume.

This document is the binding objective and rationale for that work. It is the contract the implementation and its test suite must satisfy.

## 2. Scope

**In scope**

- The Skuld broker event pipeline (`src/skuld/broker.py`, `channels.py`, `event_mapper.py`, `chronicle_watcher.py`, transports).
- The durable log adapter and contract (`src/volundr/adapters/outbound/pg_session_event_log.py`, `pg_event_sink.py`, `rest_session_log.py`).
- The read/reconstruction paths (`ws_session_replay.py`, `replay/*`, `transcript_rebuild.py`, `session_archive.py`).
- The Volundr Forge session lifecycle, live-vs-DB routing, and pod liveness reconciliation (`domain/services/session.py`, `forge.py`, `rest.py`, pod managers).
- A comprehensive automated test suite proving the invariants below.

**Out of scope**

- The web/iOS UI (this repo's UI is not the consumer of record for this effort).
- Non-Forge subsystems (ting, ravn runtime, bifrost catalog) except where they consume the event stream.
- Auth/IDP changes.

## 3. Background — the problem we are solving

A full architectural review (recorded in the design thread) established the following ground truth about the current implementation. Each finding maps to a requirement in §6–§7.

### 3.1 There is no single source of truth — there are ~4 reconstructions

| # | Reconstruction | Source | Shape | Code |
|---|---|---|---|---|
| 1 | Live fold | in-memory broker state | folded turns (uuid4 ids, inline reasoning, `metadata={usage,cost,model}`) | `broker._handle_cli_event` |
| 2 | Rebuild fold | durable log | folded turns (uuid5 ids, flush-time reasoning, `metadata={modelUsage,stop_reason}`) | `transcript_rebuild.rebuild_turns` |
| 3 | Replay-WS / cold-read | durable log | raw frames | `ws_session_replay.py`, `rest_session_log.py` |
| 4 | Archive / `.md` | snapshot of #1 | folded turns / lossy markdown | `session_archive.py` |

\#1 and #2 are **two independent implementations of the same folding contract, kept in sync only by a docstring**, with no shared code and no parity test. They already differ in turn-id policy, reasoning ordering, and metadata key names. This is the single most dangerous seam.

### 3.2 The durable log is NOT a complete superset of the live stream

`_handle_cli_event` persists transport frames first, then broadcasts — good. But several frames are **broadcast live and never persisted**: `error`, `permission_*`, `user_confirmed/active/delivered`, `available_commands`, and (depending on path) agent/subagent and plan/workflow surfaces. A log-only replay therefore omits errors and steering/permission state the live viewer saw.

### 3.3 Durability has silent loss windows

- The log buffer flushes ~every 500 ms with no WAL/disk-spill; a hard crash loses everything since the last flush.
- On backend outage the buffer caps and **silently drops the oldest frames while `seq` keeps climbing** — a permanent hole indistinguishable, on read, from "not yet flushed."

### 3.4 Inbound messages are confirmed before they are delivered

The broker records the user turn + mirrors it to the log + broadcasts `user_confirmed` **before** delivery is attempted (`broker.py:3772/3787/3804`), then delivers fire-and-forget with no retry/outbox (`broker.py:3829`). The REST bridge waits ≤3 s for an ACK and otherwise returns HTTP 200 `delivery:"unconfirmed"` (`rest.py:2461–2484`). Result: the transcript durably shows a message the agent never received, and the API reports success. **This is the root cause of the reported "the API will not receive the message" symptom.**

### 3.5 The read paths diverge from live

- **Mid-cursor replay** (`/replay?after=N`) streams only `seq > N` with **no `conversation_history`** — a mid-session attach renders a transcript that starts in the middle (`ws_session_replay.py:259–262`). Live reconnect never does this.
- **Cold-read ignores the internal-visibility gate** (`rest_session_log.py:182`); replay's default visibility is config-driven vs live's hard default; the toggle wire-message name differs between paths.
- **Live reconnect** sends completed turns + a *coalesced* in-progress snapshot, not the exact deltas emitted during the disconnect; true no-loss requires a `/log` fetch the live path never performs.

### 3.6 Volundr's view of a pod can diverge from reality

- The liveness reaper is **disabled by default**; a dead broker leaves `status=RUNNING` with a live-looking `chat_endpoint` until the next server restart.
- Local `_monitor_process` updates the state file, not the session row.
- Live-vs-DB routing keys on `chat_endpoint != null`, not a verified probe; `send_session_message`/workflow-gate endpoints 502 on a dead pod with no DB fallback; the WS proxy bypasses reconciliation.

### 3.7 A parallel, lossy, Claude-only timeline exists

`chronicle_watcher` + `event_mapper` derive a chronicle timeline from on-disk `.jsonl` — a separate pipeline that is empty for non-Claude transports and can drift from the log. It must be treated as a derived UI aggregate, or re-derived from the canonical stream.

## 4. Objectives & guiding principle

**Guiding principle: one log, one reducer, derive everything.**

1. **One canonical stream.** Every event that any client can ever see is appended to `session_event_log` first, in order, exactly once, with a monotonic per-session `seq`. The log is a *complete superset* of every live broadcast.
2. **One reducer.** A single pure function folds log frames into the turn/transcript model. The live broadcast, the replay, the cold-read, and the crash-rebuild all use it. There is no second folding implementation.
3. **Derive everything.** Live broadcast = canonical stream tee'd to connected clients. Replay/cold-read = the same frames re-emitted. In-progress load = reducer over `read_after(0)` up to head, then live tail from `head+1`. No view has private state that the log lacks.
4. **No silent loss.** Every loss mode is either eliminated or made a loud, queryable signal. Confirmed-to-the-client implies durably-recorded-and-delivered (or explicitly marked pending/failed).
5. **Truthful lifecycle.** `status=RUNNING` implies a reachable pod (or the row is reconciled on access); routing live-vs-DB is correct under crash/restart.

## 5. Canonical event model (the taxonomy we MUST capture)

Every one of the following, when it is shown to any live client, MUST be appended to the durable log with enough fidelity to reconstruct the identical view. This is the explicit "capture everything" contract.

- **Assistant output**: message starts/stops, `content_block_delta` (text + reasoning), final `result` with usage/cost/model.
- **Tool activity**: `tool_use` (name, input), `tool_result` (output, error state), permission requests/resolutions, auto-approval scheduled/cancelled.
- **Intermediary user events**: the user message itself, `user_confirmed`, `user_active`, `user_delivered` / `user_delivery_failed`, steering pending→active transitions, interrupts.
- **Agents / subagents**: `SubagentStart` / `SubagentStop`, agent attribution on frames, the running-agents set.
- **Plans & workflows**: plan creation/updates, running-agents/flock/workflow surfaces, gates.
- **Questions**: `ask_user_question` open/answer/resolve.
- **Session/activity**: activity-state transitions with `state_since`, transport lifecycle (started/stopped/crashed), available-commands catalog, build/version identifier.
- **Errors**: every `error` frame shown live.

Each frame carries: `seq` (canonical order), `kind`, `role`, `request_id`/correlation, `ts` (emission time, display metadata only), and the verbatim `payload`. Internal-visibility is a *view-time filter*, never a persistence filter — the log stores the unfiltered stream and each consumer filters its own view identically.

## 6. Functional requirements

**FR-1 — Single funnel / superset.** Every frame delivered to `channels.broadcast` (or any per-channel send) MUST have been appended to the durable log first. No code path may broadcast a frame that was not logged. Broker-originated frames (errors, permission_*, user_*, available_commands, agents, plan/workflow) are included.

**FR-2 — Capture-everything.** All event kinds in §5 are persisted across all transports (Claude SDK/tmux, Codex, Grok, OpenCode), not only those that happen to be folded into turns.

**FR-3 — One reducer.** A single shared, pure reducer module folds log frames → turns. `transcript_rebuild` and the live broker fold both call it. Turn-id policy, metadata schema, reasoning ordering, and dedup of synthetic (`conversation.turn`) vs raw frames are identical regardless of path.

**FR-4 — Gapless, idempotent, ordered log.** `seq` is monotonic and contiguous for a healthy run; `(session_id, seq)` is idempotent (at-least-once producer retries never duplicate); reads are `ORDER BY seq`. Any gap is attributable to a recorded, queryable cause.

**FR-5 — Durable delivery.** An inbound user message is not reported "sent"/confirmed-delivered until the transport has accepted it. Until then it is `pending`; on terminal failure it becomes a visible `failed` (never silently dropped). Delivery has bounded retry and survives the REST ACK window and transport warm-up. The REST contract distinguishes delivered from not-delivered.

**FR-6 — In-progress session load + live tail.** A client (LIVE or replay) attaching to a running session at any point can: (a) load the full current state via the reducer over the log up to `head`, then (b) stream new events arriving in the log from `head+1` with no gap and no duplicate. Mid-cursor attach (`after>0`) returns reconstructed history, not a mid-stream fragment.

**FR-7 — Uniform visibility & shape.** Internal-visibility filtering, its default, and its toggle wire-message are identical across live, replay-WS, and cold-read. Raw-frame shape is consistent (or explicitly documented where envelope differs).

**FR-8 — Truthful routing & lifecycle.** Live-vs-DB routing is correct: a running session serves live; a crashed/absent pod is detected and either reconciled (status corrected, endpoint cleared) or falls back to the DB transcript. `send_session_message` and workflow-gate endpoints fail deterministically and reconcile, rather than returning false success.

**FR-9 — Pod liveness.** A periodic, enabled liveness mechanism (broker heartbeat + reaper and/or connect-triggered reconcile) guarantees `status=RUNNING ⇒ reachable pod` within a bounded window. Reconciliation runs on the WS-proxy path.

**FR-10 — Chronicle as derived.** The chronicle timeline is either documented as a non-authoritative derived aggregate or re-derived from the canonical stream; it is never treated as a second source of truth.

## 7. Non-functional requirements & invariants (the test contract)

These are the invariants the automated suite MUST assert. They are the definition of done.

- **INV-1 Superset:** ∀ frame f broadcast ⇒ f ∈ log, appended before broadcast.
- **INV-2 Gapless:** N healthy emissions ⇒ `read_after(0)` returns contiguous `seq 1..N`, no gaps/dupes; overflow/crash loss emits a queryable sentinel.
- **INV-3 Idempotent:** re-appending overlapping `(session_id, seq)` leaves the row set unchanged; conflicting-but-distinct payloads are detected, not silently swallowed.
- **INV-4 Fold parity:** `reduce(read_after(0))` is content/parts/metadata-equal (under one id policy) to the live `_conversation_turns` for the same session.
- **INV-5 Read-path equality:** live (post-gate) == replay `after=0` (post-gate) == gated cold-read, frame-for-frame.
- **INV-6 Mid-cursor completeness:** reconstructed history at `after=N` ∪ tail == replay `after=0`.
- **INV-7 Delivery integrity:** a confirmed/sent message is delivered or surfaced+retried; `unconfirmed` is never treated as delivered; a malformed inbound frame never tears down the socket or drops subsequent messages.
- **INV-8 Resume monotonicity:** after head=K, a fresh broker continues at `seq=K+1`, never re-emitting `1..K`; the pre-head-fetch capture window cannot assign a colliding `seq`.
- **INV-9 Liveness truth:** `status=RUNNING ⇒ reachable pod`, else reconciled within the bounded window; dead-session WS closes deterministically; dead-session conversation falls back to the DB transcript.
- **INV-10 Visibility parity:** with internal hidden, the dropped set is identical across all three read paths; defaults and toggles match.

**Coverage:** The new/changed code targets **100% line+branch coverage** for the persistence, reducer, delivery, replay, and reconciliation modules; the repo-wide gate (85%) must not regress. Tests follow the project rules (pytest-asyncio, zero warnings, raw-SQL/mocked-pool or rolled-back real-PG, no Docker).

## 8. Architecture — the unified pipeline

```
transport frame
      │
      ▼
_handle_cli_event  ──►  normalize (uniform filter)  ──►  append to session_event_log  (seq, verbatim, FIRST)
      │                                                          │
      │                                                          ├──► tee to live channels (per-channel view filter)
      │                                                          ├──► shared reducer → in-memory turns (for snapshot/load)
      │                                                          └──► (optional) derive chronicle from same stream
      ▼
durability floor: persist-before-broadcast + WAL/disk-spill + loud overflow sentinel
```

Read paths all consume `session_event_log`:

- **Cold-read** `GET /log?after=` → raw frames (gated).
- **Replay-WS** `/replay?after=` → raw frames, paced; `after>0` first emits reduced `conversation_history`, then the tail.
- **In-progress load** → `reduce(read_after(0..head))` then live tail `head+1..` (LIVE socket subscribes with a `seq` cursor).
- **Crash-rebuild** → `reduce(read_after(0))` via the same reducer.

Delivery path:

```
inbound user msg → persist intent (pending) → deliver to transport (bounded retry)
   → on accept: mark active + log user_active + (REST) ACK delivered
   → on terminal fail: mark failed + log user_delivery_failed + surface
```

## 9. Delivery plan — epics & tickets

Implemented on `lexi/api-consolidation`, sequenced (hot files — `broker.py`, `rest.py` — are edited serially to avoid clobbering), verified between phases, committed per-epic with conventional commits.

- **Epic A — Canonical log superset & capture-everything** (FR-1, FR-2; INV-1, INV-2): single funnel guard; persist broker-originated frames; persist agents/plan/workflow/intermediary-user/error events across all transports; durability floor (persist-before-broadcast, spill, overflow sentinel).
- **Epic B — One reducer** (FR-3; INV-4): extract shared pure reducer; unify id policy, metadata schema, reasoning ordering, synthetic-vs-raw dedup; route live fold + `transcript_rebuild` through it.
- **Epic C — Durable message delivery** (FR-5; INV-7): pending/failed turn states; bounded retry + outbox semantics; REST `unconfirmed` contract; `receive_json` inside the per-message try; stale-snapshot correction at delivery time.
- **Epic D — Read-path unification** (FR-6, FR-7; INV-5, INV-6, INV-10): `conversation_history`-on-attach for `after>0`; uniform visibility gate/default/toggle across cold-read+replay+live; seq-cursored live reconnect (load state then stream tail).
- **Epic E — Lifecycle & pod liveness** (FR-8, FR-9; INV-9): enabled periodic liveness/heartbeat + reaper; reconcile on WS-proxy and message/gate paths; correct live-vs-DB routing under crash/restart.
- **Epic F — Comprehensive test suite** (all INV): the round-trip (live == persisted-PG rebuild == replay == cold-read); REST live-empty→rebuild fallthrough (un-xfail); message-not-dropped e2e; steering pending→active through the real broker; reconnect frame-diff equality; cross-transport parity (codex/grok/opencode); real-PG idempotency/seq-resume; interrupt-rebuild; permission reconnect; liveness reconcile; superset/gapless/fold-parity property tests.
- **Epic G — Chronicle disposition** (FR-10): document as derived or re-derive from canonical stream.
- **Epic H — Adversarial review** (final): independent, critical multi-agent review that the changes fundamentally close the live↔log gap defined here, with a validation report mapping every FR/INV to evidence.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Large hot files edited in parallel clobber each other | Serial edits to `broker.py`/`rest.py`; parallelize only disjoint files/tests |
| Reducer unification changes turn ids → client breakage | Single documented id policy; migration note; parity test pins shape |
| Persist-before-broadcast adds latency | Append is in-memory buffer enqueue (cheap); spill is async; measure |
| 100% coverage pressure → trivial tests | Coverage is necessary not sufficient; INV property tests are the real gate |
| Behavioral regressions across 4 transports | Cross-transport parity matrix extended to all four before merge |

## 11. Acceptance criteria (definition of done)

The branch is done when **all** of the following hold and are demonstrated by the validation report (Epic H):

1. Every FR in §6 is implemented and mapped to passing tests.
2. Every INV in §7 is asserted by at least one test; the round-trip test (INV-4/5/6) passes for every transport that the harness can drive.
3. `make verify` (ruff + full backend suite, zero warnings) passes; coverage of the changed persistence/reducer/delivery/replay/reconciliation modules is ≥ the 100% target, repo gate not regressed.
4. The reported "message not received" symptom is reproduced by a test on the old behavior and shown fixed by the new behavior.
5. The adversarial review (Epic H) finds no unaddressed gap between real-time streaming and the durable log, or every finding it raises is resolved and re-verified.
