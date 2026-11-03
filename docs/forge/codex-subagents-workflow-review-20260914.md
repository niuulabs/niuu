# Codex parallel work, plans and future requests: Lexi review

**Initial review, 14 September 2026. Proposal, not an implementation or deployment.**

**Scope correction:** the user subsequently clarified that the objective is to
understand and expose Codex's existing native subagents/APIs, not design another
workflow. Read [the focused native agent/frontend review](codex-native-agent-ui-20260914.md)
for the current answer, including live direct-input capability checks. Workflow
proposals below are historical context, not the current implementation objective.

## Recommendation

Use bounded native Codex children now for independent read/review work, retain
ordinary Forge sessions for independently owned project lanes, and expose the
structured plans/fleet that already exist. **Qualify the installed native queue
before building another queue.** Keep a Git-backed project pending-intent ledger
for ownership, dependencies and acceptance across sessions. Neither a checklist
nor a successful tool call means the work is finished.

The concrete next engineering slice is a version-qualified, identity-preserving
plan/fleet snapshot and replay contract, followed by qualification of the existing
experimental queue. Do not start a new Ting/workflow engine by default.

## Scope and evidence strength

- Source reviewed: `21bb0de6ec0ec467865382dd99640cf37b6cffdb`, isolated branch
  `xteo/codex-subagents-workflow-20260914`. This is docs over the reported Spark
  `650088f3` deployment, **not proof of Thor or Spark's loaded gateway source**.
- Own Forge session: `thor:0a713405-79b2-566c-a953-802e3a080c49`; coordinator:
  `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
- Parent process ancestry identified the **actual running** App Server executable,
  not merely `command -v codex`: standalone aarch64 `codex-cli 0.154.0`.
  Own and both child rollout `turn_context` records confirm
  **`gpt-6-astra`, `xhigh`**. Only whitelisted runtime fields were extracted;
  no credentials/config secrets or conversation bodies are published.
- Stable and experimental schemas were generated offline from that same executable.
  No feature/config change, new server, model loop, queue mutation, crash/restart,
  external Forge child, other owner's conversation, device, or product patch.
- Two authorized native read-only children completed; parent checked their cited
  schema/code and ran **196 offline tests**, all passed. This does not establish
  queue durability, cancellation cascade, UI visual quality or deployment readiness.

Evidence bundle: [runtime](evidence/codex-subagents-20260914/runtime.json),
[identity/context](evidence/codex-subagents-20260914/native-identities.json),
[fresh protocol extract](evidence/codex-subagents-20260914/protocol-extract.json),
[demo and checks](evidence/codex-subagents-20260914/README.md).
The protocol extract is an exact selected subset, not a standalone complete schema;
full generation commands and source digests are included.

## 1. Three different forms of parallel work

| Mechanism | Identity and ownership | Use here |
|---|---|---|
| Native Codex child | Native thread ID, `parentThreadId`, shared native `sessionId`, agent path/nickname/role; same Forge runtime tree | Bounded independent audit/review; parent collects and checks results |
| Ordinary Forge runner | `{instance_id, session_id}`, `SessionCoordination.parent`, objective, project/context revision; separate runtime/worktree | Independently owned iOS/runtime/web lanes and long-lived handoffs |
| NIUU collaboration | Room/participant IDs, presence, messages and replay; separate from Flokk mesh and Ravn A2A | Shared communication where configured; not an automatic Codex child scheduler |

Contracts: `src/volundr/domain/projects.py:10–27,78–91`;
`src/niuu/collaboration/models.py:9–61`, `room.py:96–101`;
`.claude/rules/ravn-niuu-boundary.md`.
Do not fabricate Forge sessions for native children or relabel them room peers.
`forkedFromId` describes inherited history, not delegation ownership.

### Native protocol capability map

The following exact types/methods are from the **fresh effective 0.154.0 schema**
in the linked extract, unless marked as observed tools or documentation.

| Concern | Native capability | Boundary / required presentation |
|---|---|---|
| Identity/discovery | `Thread.id,parentThreadId,sessionId,source,agentNickname,agentRole,forkedFromId`; `SubAgentSource.thread_spawn` has parent, depth, path | Preserve runtime-native namespace below the Forge session. `thread/list` accepts parent or ancestor filter and source kinds; omitted source filter defaults to interactive sources |
| Launch | Agent tool enum includes `spawnAgent`; our actual `collaboration.spawn_agent` ran twice | No client RPC named `spawn_agent`/`agent/spawn` in generated `ClientRequest`. `thread/start`/`thread/fork` are not automatically delegated children |
| Policy | Retained `MultiAgentMode` enum: `explicitRequestOnly`, `proactive`, or custom hint | **Corrected by second-phase audit:** the 0.154.0 input field is deprecated/ignored; schema directs clients to Ultra effort, and public V2 logic selects effort-derived policy unless overridden. Do not expose this enum as a working independent switch. See [mode evidence](evidence/skuld-cross-harness-20260914/codex-mode-contract.json). No mode/config changes |
| Subscription | Initialize/initialized; thread start/resume, unsubscribe; read/history APIs | Separate loading/subscription from read-only history. Do not promise all descendant streams or post-restart recovery from a parent subscription alone |
| Progress/tools | `collabAgentToolCall`: tool, item ID, sender, receiver IDs, optional prompt/model/effort, states/messages | Tool-call status and each child status are different. Preserve associations and result evidence, not a flat “Agent succeeded” line |
| Wait/messages | Enum includes wait, sendInput, sendMessage, followupTask, listAgents | Actual surface: `wait_agent`, `send_message`, `followup_task`, `list_agents`. Wait notification is a reason to inspect results, not acceptance |
| Completion/error | Tool: inProgress/completed/failed/interrupted. Agent: pendingInit/running/interrupted/completed/errored/shutdown/notFound | Keep native outcome/reason. A completed turn or empty result is not an achieved objective; stopped/shutdown is not success |
| Activity alternative | `subAgentActivity {id,agentPath,agentThreadId,kind}`; kinds started/interacted/interrupted/completed | This actual build can report child activity rather than a spawn/wait pair. Support both, upsert by identity |
| Cancellation | `turn/interrupt {threadId,turnId}`; tool enum closeAgent/interruptAgent | This audit exposes `interrupt_agent` but not `close_agent` or `resume_agent`. Do not assume parent interruption cancels children. Archive/delete are not cancel controls |
| Resume/history | `thread/resume`, `thread/read`, paginated `thread/turns/list`, `thread/items/list`, loaded/list | Prefer identified thread resume, verify returned ID. Read is not subscription; notLoaded is not completed. Validate descendant association and ongoing work before any explicit resume |
| Direct child input | Experimental `Thread.canAcceptDirectInput` boolean/null | Null means unavailable, not yes. Gate any direct child steer; otherwise use the parent's supported collaboration path |
| Waiting | Thread active flags waitingOnApproval/waitingOnUserInput | Show the exact child/turn asking; waiting differs from idle, stalled and blocked |

The [official App Server guide](https://learn.chatgpt.com/docs/app-server) describes
the thread/turn/item lifecycle, initialization, subscriptions, reads, resume and
turn interruption. Its current prose calls a collaboration item `collabToolCall`,
where this installed schema says `collabAgentToolCall`. Use the verified schema,
not the prose example, for adapters. It also does not document the queue methods
found below. App Server is the Codex runtime protocol—not Responses API or Agents SDK.

The [official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents)
supports explicit bounded delegation, read-heavy parallelism and care with shared
writes. Our native children share this filesystem; read-only here is an assignment
constraint, **not a separately enforced sandbox**.

### What Skuld/Forge exposes, transforms or drops

All line references below are at the fixed reviewed base. Abbreviation **C** means
[`src/skuld/transports/codex_ws.py`](../../src/skuld/transports/codex_ws.py).

| Path | Existing behavior | Gap/risk |
|---|---|---|
| C:658–733 | Bound native start/resume and ID checks | One bound parent is not a child registry |
| C:1807–1818,1884–1897 | Agent tool input keeps prompt, sender/receivers/model; result keeps states/native status | Requested reasoning effort and richer metadata not fully projected |
| C:2025–2087 | Child cards include ID, parent, name; collab states include description/result | Activity interacted/interrupted ignored; completed becomes done; shutdown becomes done; distinct failures collapse. Same-status suppression can lose enriched results |
| C:916–934 | Foreign-thread notifications become raw `agent_event`, isolating parent input/completion | No recursive normalized child transcript/fleet reducer. Raw child events do not update broker fleet directly |
| C:379–399 | Many frames gain native identity | `agent_update` is not in that enrichment list; inner parent ID is not a full envelope |
| C:1283–1369 | Worker RPC requests retain socket response routing; unknown methods rejected | Question cards at 1304–1317 lose thread/turn/item provenance; approvals retain native params but need clear owner presentation |
| C:816–870,2506–2525 | Disconnect finalizes local stranded state; interrupt targets parent | Locally marking children failed is not proof they stopped. No first-class Forge child-target control API here |
| C:1208–1216 | Some parent status/name notifications ignored; unknown parent events debug-logged | Broker “raw log” is normalized output, **not complete App Server wire capture**. Queue/goal notifications have no dedicated projection |
| `broker.py:586–595,2722–2755,3035–3047` | Current plan, running fleet, bounded recent finished fleet | In-memory state; finished retention bounded to 50, not full objective history |
| `broker_api.py:502–508,544–551` | `/api/plan`, `/api/agents?include_finished=true` | Plan read strips native/source envelope. Available via `/s/{Forge-id}/api/*` NIUU proxy (`session_proxy.py:501–528`), not an assumed dedicated Volundr plan route |
| `websocket_lifecycle.py:346–367` | Same-broker reconnect replays plan/fleet | Line 361 hardcodes `claude.agent` even for Codex |
| `broker.py:4962–5010`; `history_hydration.py:38–108` | Durable conversation/control restoration | No plan/fleet snapshot rebuild found. Reconnect to existing broker is not gateway-restart recovery |
| `event_log.py:330–365`; `broker.py:2936–2951` | Normalized frames buffered for durable log before broadcast | Buffered/overflow-limited persistence is not synchronous zero-loss acknowledgement |

Actual own-session GET `/api/agents` independently showed both native children
running simultaneously, then both done with correct parent IDs. Thus this is not
only schema inventory; native-to-Forge fleet visibility worked in the bounded demo.
No claim is made that Thor's older loaded gateway matches every reviewed-base detail.

## 2. Task management: do not confuse five native/conventional layers

| Layer | Native today / convention / gap |
|---|---|
| Structured progress plan | Native `turn/plan/updated {threadId,turnId,explanation?,plan:[{step,status}]}`; pending/inProgress/completed. C:1115–1140 already maps it to `plan`, dropping explanation. No task IDs, owners, dependencies or automatic dispatch in this shape |
| Plan-mode document | Native public plan text item plus delta, distinct from structured steps; C:965–970,1849,1927 preserves public text. Not permission to execute |
| Thread goal | Stable goal set/get/clear, objective/status/token accounting. C:2670–2700 maps `/goal`. Native lifecycle includes active/paused/blocked/usageLimited/budgetLimited/complete. Not a project backlog |
| Future input queue | Native **experimental** RPCs below; not mapped in audited ordinary Skuld input path. Presence is proven; durability and dispatch semantics are not |
| Project objectives/pending tasks | Forge metadata/receipts + Git conventions already usable. Rich task ownership, dependency, acceptance, ordering and recovery need an explicit ledger/contract, not a fabricated native feature |

The [official Goals cookbook](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
describes persisted thread goals and continuation at an eligible idle boundary,
subject to pending input/work and interruption rules. Plan-only work does not create
automatic continuation; interruptions can pause goals. This useful native behavior
does not promise unattended project scheduling. No goal was created for this audit.

The effective tools here do not expose `update_plan`, although the protocol and
adapter support structured plans; own `/api/plan` returned empty. This is **not proof
Codex lacks plans**. The report checklist is a human-readable audit record, not a
pretend native plan tool run.

### Installed native queue contract (experimental)

| Method | Input | Output |
|---|---|---|
| `thread/queue/add` | threadId, clientUserMessageId, input | queuedSubmission |
| `thread/queue/list` | threadId, optional cursor/limit | data, nextCursor |
| `thread/queue/update` | threadId, queuedSubmissionId, input | queuedSubmission |
| `thread/queue/delete` | threadId, queuedSubmissionId | deleted boolean |
| `thread/queue/reorder` | threadId, queuedSubmissionIds[] | empty response |
| `thread/queue/start` | threadId, optional queuedSubmissionId | turn |

`QueuedSubmission` contains **id, clientUserMessageId, input**. The stable schema
contains `thread/queue/changed {threadId}`, but stable `ClientRequest` excludes all
six queue RPCs. The notification invalidates a list; it is not a versioned snapshot.
C:644–650 already requests experimental API capability at handshake, but that is
not a verified queue implementation or permission to invoke it.

Before reuse, establish: persisted-before-ACK boundary, process-loss survival,
duplicate add semantics, atomic claim/pop/start, active-turn behavior, automatic
versus explicit consumption, pagination/order revisions, admission bounds and
result correlation. The schema alone establishes none of these. No priority,
dependency, owner or acceptance fields exist in `QueuedSubmission`.

### What happens to new input now

`broker.py:3876–3953` echoes a user message with stable identity and pending delivery;
4113–4168 selects routing using current transport activity. C:2528–2563 sends
`turn/steer` with `expectedTurnId` for live steering. A matching acknowledgement
marks that input consumed/active, **not fulfilled**. C:2565–2603,2971–2994 separately
implements interrupt-and-replace redirect using in-memory coalesced strings;
that is not a durable “after the present objective” queue.

The existing delivery store is valuable but different:
`src/volundr/domain/message_delivery.py:1–25` and
`src/volundr/adapters/outbound/pg_message_delivery.py:20–94` implement durable
at-most-once claims, payload conflict detection and claimant-owned settlement.
Uncertain claims are not stolen/resubmitted after restart. This prevents some
duplicate execution but does not guarantee eventual execution of pending tasks.

**Proposed distinct intents:**

- **Steer current work:** exact target turn; preserve earlier objective and tool state.
- **Do later:** immediately echo a distinct request and record a durable queued intent;
  do not inject it as active steering. Dispatch at its explicit eligibility barrier.
- **Run in parallel:** new bounded assignment with independent owner/attempt identity;
  consume a concurrency slot only when eligible and authorized.
- **Stop/replace:** explicit cancellation scope and acknowledgement, not implied by
  a normal new message or a queue reorder.

If only chat is available today, the coordinator records “do later” in the project
ledger at the next handling boundary and acknowledges it without dropping the active
objective. This is a convention, **not a guarantee of pre-turn durable queue ingress**.
The later API must persist and echo before claiming “saved for later”.

Keep three state axes distinct: **delivery** (accepted/uncertain/consumed), **work**
(queued/running/waiting/blocked/completed/cancelled/failed), and **acceptance**
(unreviewed/accepted/rejected). Disconnected/unknown is an observation state, not a
successful or failed objective. Completed runtime work remains unreviewed until
evidence is checked. Equal text under different request IDs is two requests; a retry
under the same ID is one delivery. Never deduplicate on text.

## 3. Minimal workflow usable by this project now

1. **Capture and checkpoint.** Coordinator owns objective, scope, completion evidence,
   source/worktree, dependencies, next checkpoint and pending-intent ledger in Git.
   Assign a stable task ID separate from message, native thread and Forge session IDs.
   Typed receipts remain the durable cross-session return path.
2. **Delegate proactively but boundedly.** Start with a parent plus **two read-only
   children maximum**, no child fan-out and no same-file parallel writes. Give each
   an independent deliverable and source boundary; parent performs useful integration
   work while children investigate. Distinct implementation lanes use coordinator-
   authorized ordinary Forge runners/worktrees, not native children pretending to
   own other runners' branches.
3. **Make budgets explicit.** Proposed default review budget: 15-minute checkpoint
   per child, one evidence-bearing final, milestone on material findings/blockers,
   zero automatic relaunches. Concurrency/admission limits should be configurable.
   No numeric token/dollar cap was requested or enforced in this demo; do not imply
   a prompt budget is a hard runtime cap. Ask/record a spending envelope before
   broader paid work; native goal budgets are not assumed fleet-wide quotas.
4. **Monitor objectives, not heartbeats.** Record last useful evidence, status and
   observed time for each track; use agent messages/list/wait without busy polling.
   At a stale checkpoint inspect/ask once; report blocked or unknown if unresolved.
   Do not repeatedly restart, silently reassign, or treat liveness as progress.
5. **Barrier before synthesis/integration.** Each independent track returns findings,
   exact evidence, checks and uncertainty. Parent opens the evidence and performs
   risk-appropriate tests. Missing evidence means waiting for evidence/rejected,
   not completed-and-accepted. Dependent implementation waits for both accepted
   contracts and the relevant owner's release barrier.
6. **Capture new requests without replacing current objectives.** Ledger fields:
   task ID, original request ID/reference, trusted author/origin, intent, objective,
   state, owner, dependencies, priority/order, revision, budget, native queue ID (if
   used), attempts, evidence and next action. Keep one writer/owner per record.
   Default FIFO among eligible same-priority tasks; blocked tasks stay visible.
   Reordering queued work does not preempt running work. No fictional future tasks
   were added by this review.
7. **Recover conservatively.** On interruption, checkpoint completed evidence and
   pending work. On reconnect, compare ledger, receipts, known native identities
   and snapshots before choosing an action. Resolve uncertain dispatch by inspection,
   not blind resend. Resume only explicitly authorized work; do not spawn replacements
   because a socket vanished.
8. **Human communication.** Send useful milestone/blocker/initial-review completion
   updates via the existing local OpenClaw route. Keep technical IDs in linked
   evidence/receipts. Use the one shared hourly project reporter; create no new cron.
   End this initial assignment with a checked handoff, but keep this session open
   for user iteration—no archive and no claim of continuous unattended monitoring.

This run exercised that workflow: two parallel read tracks, parent runtime checks,
status messages, wait, final evidence inspection and offline tests. Cancellation,
failure recovery and queue mutation were deliberately left to future acceptance
tests, not demonstrated by fabricated runs.

## 4. Small implementation proposal — separate later assignments

| Priority | Change | Acceptance gate / ownership |
|---|---|---|
| P0 now | Adopt bounded delegation, evidence barrier and Git pending-intent ledger | Coordinator-owned process; no runtime deployment needed |
| P1 | Preserve native plan/child identity, status/reason, timestamps, source and result enrichment; provide versioned recoverable plan/fleet snapshots plus cursor replay | Runtime owner reviews as a later scoped contract change. Reuse existing event/replay infrastructure, do not duplicate current steering/timeline/reducer patches |
| P1 | Qualify experimental queue against exact effective runtime in an isolated disposable test environment | Separate authorization before live mutation/crash tests. Publish supported/unsupported/unknown per capability, not a single blanket “Codex supports it” flag |
| P2 | Thin Skuld queue port/adapter and harness-neutral ingress/actions, using native queue if qualified; persistent project intent/attempt correlation in Volundr where needed | Explicit queue versus steer, replayable user echo, idempotency and conflict semantics. If native durability fails, document failure before proposing minimal additional store; no new scheduler by default |
| P2 | iOS/web render shared activity hierarchy, plan, queue and action acknowledgements | Existing owners, after current release/replay work. No patching their moving branches in this assignment |

The API contract should carry Forge reference, native thread/parent/session-tree
IDs, task/attempt/request/submission IDs, native tool/item/turn IDs, version/cursor,
trusted author kind/reference, runtime observation versus work/acceptance status,
last activity, reason, evidence references and declared capability limits. Child
question/approval responses must route to the exact pending owner. Reorder/update
needs revision/conflict handling; cancellation needs target scope and observed stop,
not just delivery acknowledgement. Unknown capability must be visible, never a
silent fallback to steering or replacement.

For product structure: Volundr owns Forge/project intent persistence; Skuld adapts
the Codex runtime; NIUU supplies genuinely shared transport-neutral mechanics via
ports. Keep runtime adapters behind ports and composition in existing roots. Do
not relocate judgment to NIUU or require resident/Ravn collaboration for ordinary
Codex sessions.

### Presentation requirements, not claims about uninspected clients

- Project → Forge runner → native child tree; parent objective remains visible.
  Show plan, current work and future requests as separate sections, with queued,
  running, waiting, blocked and completed states and an evidence-review badge.
- Expand a child for objective, last activity, native status, result and evidence;
  distinguish unknown/stalled from failed and stop requested from stopped.
- Composer exposes current-work versus later intent and preserves immediate original
  user echo. A queued instruction is not hidden in an internal status bubble.
- Preserve same-text separate requests and stable row identity across live/history
  reconciliation. Show priority/order edits as task edits, not new user messages.
- Internal messages are visibly internal with structured provenance. Ordinary
  `/messages` currently forces `type:user` (`src/volundr/adapters/inbound/rest.py:2913–2914`),
  then broker human attribution (`broker.py:3908–3935`). A prefix cannot fix authorship.
  Continue using typed receipts rather than injecting internal status as human text.
- Reviewed NIUU web hook has no plain plan/agent_update/agent_event handling and
  defaults to no-op (`web-next/packages/ui/src/chat/hooks/useSkuldChat.ts:2014–2015`).
  Its `room_agent_event` branch at 1885 is different. Separately owned Lexi web/iOS
  worktrees were not inspected or modified; their current rendering is unverified.

### Acceptance matrix

“Offline checked” below means existing mocked tests passed, not live queue/UI proof.

| Scenario | Required result | Evidence today / next test |
|---|---|---|
| Two children + new later request | Parent keeps objective; two distinct running tracks; later request visibly persisted/echoed without being steered; eligible dispatch only once | Two-child fleet live proof; combined queue scenario **not run** |
| Child completes | Tool/turn completion separate from evidence acceptance; parent inspects exact result before integration | Both real child reports inspected; offline collab pairing/status tests pass |
| Child completes without evidence | Remain unreviewed/waiting-for-evidence; no false achieved objective | Required acceptance test; optional native state message may be absent |
| Child fails/stalls | Preserve error vs interruption vs unknown; alert owner at checkpoint; parent can finish independent tracks | Offline failure/status tests pass; live stall/budget recovery not tested |
| Parent interruption | Clearly scoped interruption; enumerate children still running; no implied cascade/restart | Parent control inspected; cascade behavior **unknown** |
| Client reconnect | Exact native identity, no duplicated rows/tools, preserve source; fresh snapshot plus newer events | Same-broker cache code/tests; Codex hardcoded Claude replay label found |
| Gateway/App Server restart | Queue/plan/fleet recover from persisted state; uncertain claims remain visible, not resent; never reset objective silently | No live restart; plan/fleet rebuild gap found; queue durability unknown |
| Cancel child/queued/running task | Targeted acknowledgement plus observed stop/removal; preserve history; unaffected siblings continue | Protocol available, no live cancel demo. Queue delete is not running-task cancellation |
| Duplicate input/result | Same request identity one delivery; native item/state upsert; newer evidence enriches same status | Offline identity/delivery/dedup pass; same-status enrichment gap found |
| Equal text, distinct requests | Two independent echoes/tasks/IDs; neither swallowed | Identity contract reviewed; explicit end-to-end queued UI fixture still needed |
| Reorder/priorities | Revision conflict on stale reorder, deterministic eligible order, no implicit preemption | Native reorder shape only; behavior/concurrent writers unqualified |
| Budget/backpressure | Bounded active/pending work; reject/hold explicitly; usage-limit distinct from success; no retry storm | Demo capped at two children total; no hard token cap or overload test |
| Parent/child approvals | Exact thread/turn/item ownership, explicit waiting, response cannot land on sibling | RPC routing inspected; normalized question provenance gap found |
| Internal progress | Trusted agent/system origin survives ingress/live/replay; never human-authored because of role or text prefix | Typed project receipts used; ordinary-message provenance gap documented |
| Version disagreement | Effective executable + schema + observed behavior reported separately from configured/PATH versions | Proven for this Thor session; Spark runtime not independently inspected here |

## Initial checkpoint and remaining ownership

- [x] Read instructions/project/coordinator/notification contracts and verify clean base.
- [x] Verify own effective executable, schema and parent/child Astra/xhigh context.
- [x] Launch exactly two native read-only children; inspect running and completed state.
- [x] Review child evidence, run scoped offline checks, record report/proposal.
- [ ] Coordinator acceptance and future implementation selection; **not automatic**.
- [ ] Direct user iteration; session remains open, no orphan active child work.

Runtime owner `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2` retains current steering,
timeline/replay and safety/deployment work. This review authorizes no restart.
Native owner `thor:ac1b685f-59c6-5434-a85a-ac4d435077a3` retains current publication
and replay work afterward. Project UX owner
`thor:a11526be-206c-5337-8606-c949f96b6408` retains presentation work.
These findings are coordination input, not permission to merge their branches or
call a proposed capability shipped.
