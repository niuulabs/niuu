# Steering chronology and duplicate-display handoff

September 14, 2026. Server/runtime worker:
`thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`.
**Native implementation required for reliable live reconciliation; this is not an
assignment to the chat-polish runner, a native release reservation, or device QA.**
The coordinator should route the bounded native work to a dedicated session.

## Observed user report and baseline

User says **“the latest build”**, not an exact Settings/About number. Project
records identify the latest delivered iOS build as **2.0 (2263)**, signed source
`f9b78bd1`; the source below was inspected at that pin, not a moving worker HEAD.
Do not describe this as a verified installed-device version.

Affected session: `spark:e93a4633-c288-519c-bb6b-7dc2274aa2e2`,
`lexi-spark-codex-server-test`, native thread
`01a09ed2-a7b6-7e23-9e01-94a77aff208d`. Its gateway uses the September 13
`f5d7a5e49eafd765f8daf53bdc51c95ac1949939` candidate.

Read-only conversation and 258 public log frames show:

1. Initial input at 07:30:00 UTC.
2. Public assistant/tool activity from 07:30:05.
3. “Steering to speak French” accepted by the broker at
   `07:30:56.168157+00:00`; native acceptance observed at `07:30:56.178974`.
4. More assistant/tool activity, including a French response.
5. “Back to English” accepted at `07:31:23.040438+00:00`; native acceptance
   observed at `07:31:23.049195`.
6. Further activity and final answer at approximately 07:32.

Both native acknowledgements identify the **same** executing native turn
`01a09ed2-a82e-72a2-9556-539b4179ea01`. There is no need to replace working
`turn/steer` with interruption or new-turn dispatch.

The old canonical response instead contains **three user rows followed by one
29-part assistant row**. Those parts contain all pre/between/post-input output.
The user timestamps exist, but `user_confirmed` omits them and the cumulative
assistant is dated at completion. No duplicated canonical row was found. A static
native merge review supports the reported transient-display failure, but does not
constitute a rendered reproduction.

## Server contract in this candidate

- One broker-observed input timestamp is reused for the user row `created_at`, raw
  input log timestamp, and `user_confirmed.created_at`.
- `user_active` includes that same `created_at` and, where native acceptance was
  observed, `accepted_at`. Canonical user metadata includes
  `steering_accepted_at`. These distinguish broker insertion from native acceptance;
  neither claims to measure the model's internal token-level consumption.
- When a transport declares live steering and durable event logging is enabled,
  frames carry `conversation_timeline: {schema: 1, seq, observed_at}`. `user_confirmed`
  carries the original input anchor, not its later confirmation position. A
  `user_active` marker observes the lifecycle ACK itself, not the input slot;
  recover input placement from the correlated canonical row or original
  confirmation (its `created_at` still names the original input). The outer event
  ledger sequence/cursor still identifies the individual echo event.
- Native assistant parts preserve their first observation in the same marker,
  plus `span_seq` identifying the native accumulation span. Later completion
  updates the existing item. Public native `id`, `thread_id`, `turn_id`, phase,
  tool call/result identity, exact command and output remain intact.
- The shared read projection yields chronological **display fragments**, not
  additional native turns. An assistant fragment has a stable UUID `id`,
  first-observed `created_at`, and
  `metadata.conversation_timeline.fragment: true`. Parts remain authoritative.
- Example order: assistant A, user steer 1, assistant B, user steer 2, assistant C.
  All three assistant fragments may share one native turn ID. That native turn ID
  therefore **cannot identify a whole display row**.
- If an item began before input and finishes afterward, its authoritative complete
  text/output enriches its original slot. A tool result stays with its call. This
  is item-level chronology, not invented character/token timing.
- Fragment IDs remain stable across polls, native completion, REST, WebSocket
  reconnect, saved conversation-cache reload, and durable replay. Only the final
  native slice retains turn accounting/terminal status. At most one returned
  fragment carries top-level `in_progress: true`.
- `projection_revision` gains `;timeline-1`. Ordinary appends/completion do not
  rotate the revision. A client must invalidate its old positional projection
  when the revision changes; retain existing `after_id` seam validation.
- Unknown or partially timed legacy spans remain unchanged. This is not a
  retrospective migration, and the affected old gateway/log is not rewritten.
  A later historical repair needs separately verified evidence and authorization.

REST and snapshot arrays already have the authoritative display order. Do not
sort them by native turn ID, local send date, or text similarity. Marker `seq` is
session-scoped and useful for positioning pending live input; it is not a global
clock and not a replacement for the outer event replay cursor.

## Required native work (read-only findings at f9b78bd1)

File: `apps/chat/LexiChat/V2/Views/CodeSessionLiveModel.swift`.

1. `user_active` immediately triggers `reconcile()` (around line 2806). Preserve
   that useful refresh, but make the refresh idempotent during live streaming.
2. Around lines 1762–1773 the merge invokes
   `canonicalTurnsPreservingSteeringOrder`/`preservingUnpositionedSteering`, then
   selects a replacement by the first shared text item **or merely shared native
   turn/thread**. It requires that one row to cover the entire cumulative local
   builder. One new canonical fragment cannot cover that whole builder.
3. `canonicalTurnsPreservingSteeringOrder` (around line 1847) splits/retains frozen
   local prefixes using an 80-character text-prefix heuristic against one
   cumulative canonical assistant. Multiple steers and polls can keep a prefix
   after its actual items have already become canonical elsewhere.
4. `ForgeTranscriptView.swift` labels local `lexi_recovered_echo` or
   `lexi_unpositioned_echo` rows “Steering message · position unconfirmed”. This is
   a client recovery flag, not a string emitted by Codex or a missing native API.

Implement the new revision path using authoritative **display row ID** plus
native **item membership**, rather than matching a whole assistant solely by its
native turn ID. Coalesce optimistic user rows using server `id`/`request_id`;
replace local guessed dates with `created_at`. Clear recovered/unpositioned flags
only when the correlated canonical echo and its authoritative position are known.
Do not simply hide genuine uncertainty in legacy/offline history.

During a refresh, collect the identities represented across **all** canonical
fragments for the active native turn. Retire only local text/tool items covered
by those authoritative items; retain any genuinely newer live-only items. A late
completion updates its existing item in the earlier fragment. Do not append a
second copy, drop newer suffix output, or let a shallow snapshot replace full
cached tool output with an empty value. Full-result retrieval remains available.

Native normalization must retain the additive markers if it needs live positioning.
Reconcile the canonical display row IDs without treating them as new provider turn
IDs. Keep current legacy-server compatibility, cursor gaps/repair revisions,
message provenance, real user steering, permissions, and send-once recovery.
No workflow engine, new control protocol, or native slash-command redesign is part
of this handoff.

## Acceptance matrix for the native owner

Use a **new Spark gateway on the candidate source**. An API-only cutover deliberately
keeps the existing gateway's old loaded code; health of the API is not adoption
proof. Do not restart the user's existing gateway to obtain new-source evidence.

- Stream commentary, a long-running tool and more commentary; steer twice during
  the same native turn. Include identical-text steers with different request IDs.
- Verify input dates against REST/log/echo and placement against the first-observed
  item boundaries; verify no interrupt/new-turn dispatch and no duplicated native
  text item/tool ID.
- Poll repeatedly after each `user_active`, while a pre-steer item/tool completes
  after input, and immediately before/after the native result.
- Compare rendered item identities and ordering to canonical REST, not just row
  counts. Confirm the two inputs stay between the correct activity fragments.
- Close/reopen, background/reconnect, process-relaunch, and page an overlapping
  recent window with `after_id` and projection-revision changes. No resend.
- Exercise shallow/large tool snapshots and late full-output recovery; no text
  prefix duplication, lost suffix, blanked output or invalid whole-turn replacement.
- Exercise old servers/untimed history, failed/uncertain steering delivery,
  unavailable canonical echo and an input sent at turn-completion race.
- Record exact Settings/About build, session source/adoption, raw/REST/rendered
  evidence and device/simulator limits. No physical device evidence exists yet.

## Server evidence and limits

`tests/test_skuld/test_steering_timeline.py` uses the real broker and Codex adapter
with a hermetic RPC/ledger fixture: two `turn/steer` calls, stable IDs and exact
parts/timestamps across live REST, recent/full WebSocket snapshots, file reload,
raw replay and seeded replay. A crash-tail regression ensures a later user seed
cannot hide assistant output preceding that input. Pure projection tests cover
idempotence, legacy barriers, identical input text and late tool results.

Private read-only capture and logs are in the runtime checkout's
`.local/codex-steering-replay-20260914/`. An **offline counterfactual**, stamping the
retained raw observations, turns the affected four-row transcript into six ordered
rows, preserving every original assistant part payload (29/29) and each human
identity/content. It is not deployed output or a database backfill. The original
canonical user dates precede raw log observations by fractions of a millisecond;
the new broker removes that difference for newly recorded inputs.

## Follow-up from the fresh timeline-v3 gateway

[Live deduplication and filesystem archive follow-up](codex-live-dedup-followup-20260914.md)
adds real fresh-gateway evidence, precise frozen-tool/result and mutable-cache
boundary findings, an explicit native-owner escalation, and a shared synthetic
fixture. It also corrects the prior overly broad archive parity claim: the
filesystem archive normalization path was omitted despite reducer/cache parity.
