# Skuld agents, plans and control API

## Executive assessment

Skuld should expose **one capability-aware interface over native runtimes**, not
make every harness imitate Claude or introduce another orchestration engine.
Codex, Claude, Grok and Muse already provide substantial child-agent control.
Pi deliberately leaves child agents and planning to extensions. A uniform UI is
feasible, but uniform buttons must not promise uniform semantics where none exist.

The most urgent gaps are truthful identity, capability reporting and recovery—not
agent launching. Existing Skuld adapters sometimes flatten children into tool rows,
omit planning events, or conflate idle with finished. Two resume paths can create a
fresh conversation where a client expects continuity. These findings are reasons
to qualify adapters before enabling stronger UI controls, not permission to patch
or deploy the current owners' work.

**Proposed product rule:** every supported native capability should be discoverable
and usable through the Skuld API by authorized human and agent clients. An unavailable,
disabled, unsupported or not-yet-adapted capability should be visible with its reason,
not represented as an empty successful result. Specialized capabilities remain
namespaced and typed rather than being lost to a lowest-common-denominator design.

This document is a research-backed **proposed contract**, not an implemented API.
The requested harnesses are Claude Code/tmux, Codex, Grok, Pi and Meta Muse Code.
OpenCode is a supplementary comparison because Skuld also registers it. “Build” is
not another requested harness; OpenCode's Build role is also not a separate runtime.

## Evidence boundary

Assessment date: September 14, 2026. Local product source is fixed at
`21bb0de6ec0ec467865382dd99640cf37b6cffdb`; this branch adds research documents.
Current public documentation can describe newer native features than an installed
or configured launcher. No other conversations or remote hosts were exercised.

| Harness | Version/source evidence | What this does not establish |
|---|---|---|
| Codex | Own effective App Server executable **0.154.0**, generated schemas, two real native child threads and 11 read-only RPCs in the [earlier focused audit](codex-native-agent-ui-20260914.md) | Universal direct-child input, restart/cancel guarantees or another host's effective runtime |
| Claude/tmux | Child reported read-only PATH binary `2.1.271`; current official docs; local hook/TTY adapter inspected | Effective executable, model or tools of any existing Claude session |
| Grok | Vendor source pinned at `37949780c144e37df692e3d669051a21fec24f20`; current official docs | Installed version or enabled experimental extension features |
| Muse | Public MSP/SDK pinned at `fbce769ccb75ab971d00e01a00fe076de4c773fc`; local tests reference Muse 1.0.2 | Deployed binary schema or independent proof of its durability claims |
| Pi | Current upstream docs; repository's earlier validation records **0.85.1** | Current launcher/version, loaded extensions or their capabilities; `pi` was absent from this shell's PATH |
| OpenCode | Current official agents/server documentation; local HTTP/SSE adapter | Installed version; `opencode` was absent from this shell's PATH |

The register and transport definitions are in
[`src/niuu/config_models.py`](../../src/niuu/config_models.py):81–204.
Absence from PATH is not evidence that a configured absolute-path launcher is absent.
Public sources are linked near claims; source hashes, test results and qualification
limits are in the [evidence record](evidence/skuld-cross-harness-20260914/README.md).

**Codex mode correction:** the effective 0.154.0 schema retains a `MultiAgentMode`
type, but marks `TurnStartParams.multiAgentMode` deprecated and ignored, directing
clients to `effort:"ultra"` for proactive delegation. Do not expose the retained
enum as an independently functioning control. Plan/Default remains a separate
mode. The [extracted contract](evidence/skuld-cross-harness-20260914/codex-mode-contract.json)
corrects the earlier audit's interpretation; no runtime setting was changed.

## Shared language

| Term | Common meaning | Must remain distinct from |
|---|---|---|
| Harness | Execution runtime and its installed protocol/capabilities | Model or reasoning effort |
| Agent definition | Reusable role, instructions and allowed tools | A running agent |
| Agent instance | Addressable runtime worker, with native identity and activity | A tool invocation or OS process alone |
| Subagent | Agent instance delegated by another instance | A history fork, unrelated session or generic subprocess |
| Teammate | Agent instance participating in a native team | Every non-primary tmux pane |
| Session | Durable conversation/runtime context; Forge and native IDs are separate namespaces | An individual turn or a successful objective |
| Turn/attempt | One execution episode, potentially interrupted, resumed or replaced | Agent lifetime |
| Plan | Proposed approach or progress snapshot, with an explicit kind | Authorization, executable dependency graph or scheduler |
| Task | Identified work item when the source provides such an object | A plan step without stable identity or a background shell job |
| Goal | Persisted objective with its own lifecycle where provided | The next queued user message |
| Submission | A particular input with identity, target and delivery policy | Its text or the eventual work result |
| Native cross-task reference | A client-authorized reference to another runtime conversation | Checklist task ID or membership in the native child tree |
| Message | Communication from a known actor to a target | Human authorization or necessarily a new task |
| Result | Runtime outcome plus available artifacts/evidence | Independently reviewed acceptance |

Use “agent-to-agent messaging” for the general capability. Reserve **A2A** for the
actual protocol. Native Codex collaboration, Claude team/inbox messages, NIUU rooms,
Flokk mesh and Ravn A2A are not interchangeable. Skuld adapts runtime/channels;
shared communication mechanics belong in NIUU, while Ravn retains its judgment and
A2A semantics. See the binding [ownership rule](../../.claude/rules/ravn-niuu-boundary.md).

## Agent capability comparison

**N** = native documented/source-defined; **E** = extension-dependent;
**U** = not established in this audit. Native availability does not imply that the
installed runtime enables it or that Skuld exposes it today.

| Harness | Launch and hierarchy | List, status and inspect | Steer/message and wait | Stop and resume |
|---|---|---|---|---|
| **Claude/tmux** | N: `Agent` tool, foreground/background children; separate experimental teams | N: child IDs/transcripts, team views; `claude agents --json` inventories independent native sessions, not a substitute for the child tree | N: `SendMessage` for resumable general/custom children and teammates; direct team-pane interaction; peer inbox support is separately enabled | Native task/agent stops and team shutdown; one-shot Explore/Plan differ from resumable children; team recovery has limitations [C1][C2][C3] |
| **Codex** | N: model-issued native spawn tools; `parentThreadId`, role/name; no equivalent general frontend spawn-child RPC in audited union | N: thread listing with child source filters, thread status/read/history; own two children were readable | Native model messaging/follow-up/wait. Direct client `turn/steer` is not universal: both audited children had `canAcceptDirectInput=false` | Native model interrupt/close/resume vocabulary; client turn interruption is separate. Child cancel/cascade/restart semantics need qualification [O1] |
| **Grok** | N: child sessions, native subagent ID plus parent/child session IDs | Source-defined `x.ai/subagent/list_running` and `/get`, progress/output/error snapshots | `/get` supports bounded blocking; `/message` steers or queues, **feature-gated**; ownership and saturation outcomes explicit | `/cancel` distinguishes cancelled, already finished and not found; child-resume API not established [G1][G2] |
| **Muse** | N: durable child ID, child session ID, nesting, role/objective and control state | Child transcript through `session/read`/`view/page`; child result includes artifact/evidence references | N: `subagent/sendMessage`, `followupTask`; native child control lifecycle | N: distinct interrupt/stop/close/resume/reopen/readResult contracts; installed conformance untested [M1] |
| **Pi** | No built-in child orchestration in core; E: official example launches isolated Pi processes | E: example streams tools, parallel progress and per-agent usage | Core RPC controls the addressed session; peer messaging and child handles depend on the extension | Example propagates abort to children; continuation/control are extension-specific, not a core team API [P1][P3] |
| **OpenCode** *(supplementary)* | N: primary/subagent roles and child sessions; Build/Plan are roles | N: children, status, message history and SSE; CLI child navigation | Session message APIs exist; exact concurrent child steering/peer mailbox semantics U | N: session abort and persisted session addressing; inspect effective version before promising active-turn steering [OC1][OC2] |

**Native communication detail.** Claude's cross-session inbox distinguishes delivered,
held and refused messages; receipt of another agent's message is not user consent
([C4]). Grok's child-message source distinguishes saturation, uncertain admission,
not-active and rejected delivery ([G3]). Codex's tool-level send-message and
follow-up are separate: the former need not wake an idle child, the latter can.
Muse similarly declares message versus follow-up controls. A shared API must retain
these distinctions rather than collapsing them into “send prompt.”

## Planning, tasks and pending-input comparison

| Harness | Planning mode | Structured progress / tasks | Persistence and future input | Important limit |
|---|---|---|---|---|
| **Claude/tmux** | Native Plan Mode; separate plan approval/permissions | `TaskCreate/Get/List/Update` with IDs; team ownership/dependencies; legacy `TodoWrite` | Tasks can persist and survive compaction. Busy composer messages may enter the **same turn** after tools | Task tools are model/config-dependent in recent versions; newer models including Opus 5 do not get them by default [C5][C6] |
| **Codex** | Native Plan/Default collaboration modes | `turn/plan/updated` steps/status; plan-text item; separate persistent thread goal | Experimental `thread/queue/*` add/list/update/delete/reorder/start; crash/dispatch semantics not established by schema | Progress steps have no native task IDs/assignees/dependencies; no generic team task-board API found [O1] |
| **Grok** | Native plan mode and restricted plan child type | ACP plan entries with content, priority and status | Vendor documents resumable sessions and separate background workflows; future-input details are operation-specific | Parent Plan Mode does not itself edit-gate children or make every shell command read-only [G4][G5][G6] |
| **Muse** | A distinct planning-mode API was not established; do not relabel approval policy as such | Native todo snapshots and goal state; todo entries have text/status/activeForm, **not stable IDs in inspected schema** | `turn/start ifBusy:queue|steer|replace`; snapshot queued turns; exact `turn/unqueue`; vendor documents durable acknowledgements | Snapshot revision is explicitly diagnostic, not an ordering guard; queue reorder/task assignment not established [M1][M2] |
| **Pi** | E: official plan-mode example, not core feature | Example extracts plan steps and completion markers, persists extension state; not a native shared task board | Core `steer` versus `follow_up`; session JSONL history; pending-queue crash durability U | Current steering boundary is after current assistant tool calls, before next model call; older docs differ [P1][P2][P4] |
| **OpenCode** *(supplementary)* | Native Plan role with permission restrictions | Native session todo read API | Persisted sessions; async prompts; durable reorderable future-objective queue U | Todo capability is role-dependent; not all child roles have it [OC1][OC2] |

Plan mode, written plans, checklist tasks, background jobs and queued submissions
are five separate surfaces. The UI should show which exist, not synthesize a task
board from every numbered paragraph. A missing plan can mean no plan emitted,
tools not enabled, unsupported native feature, adapter gap, or stale state.

Recent Claude documentation additionally states that a teammate plan request is
approved on arrival in the lead session without lead review; tool permissions still
apply. This must not be labeled a human or evidence-review gate ([C2], section
“Have teammates plan before implementing”).

Pi's official examples illustrate the extension boundary: the subagent example
provides single/parallel/chain work with bounded concurrency; the plan example uses
text extraction and completion markers with persisted extension state. Those are
real example implementations, but not installed capabilities of every Pi session.
Their metadata must identify the extension and version ([P3][P4]).

Grok also has a distinct native workflow layer: phased background agent runs,
saved progress, per-agent visibility and agent-count budgets. The vendor describes
pause/resume without repeating finished work and a `/workflows` view. Treat that as
a native workflow capability to expose—not proof that ACP's ordinary plan entries
are executable workflows, and not a reason to install a second workflow engine.
Published scale/budget claims are not benchmarks from this audit ([G6]).

## What Skuld actually exposes at the pinned source

The shared [`TransportCapabilities`](../../src/niuu/ports/cli/transport.py):12–36
describes transport-level messaging, steer, resume, permissions and terminal
features. It has no child-discovery/control, task, queue or goal capability model.
The existing [`broker_api.py`](../../src/skuld/broker_api.py):492–551 routes expose
`GET /api/capabilities`, `/api/plan` and `/api/agents`, but not the comprehensive
native control surface. Empty `/api/plan` cannot distinguish unsupported from absent.

| Adapter | Present projection | Missing or misleading boundary |
|---|---|---|
| Claude tmux | Hooks, legacy TodoWrite plans, Task-derived child rows, pane controls | `Agent` rename and new Task tools not handled in specialized paths; latest-active-child attribution cannot prove parallel ownership; child transcript hook field mismatch; idle teammate becomes done |
| Codex | Structured plans, collaboration tool details, partial child rows, raw foreign-thread events | Raw child events do not comprehensively update fleet; child controls/queue/goal absent from shared API; plan explanation/context lost; disconnect can be labeled failure |
| Grok | Generic spawn/tool rows; ACP plan retained inside system content | No top-level shared plan/fleet fold; nonterminal tool updates dropped; inner update loses outer session context; root steer interrupts; resume advertises more than code establishes |
| Muse | Generic child Task row and typed todo plan; native root steer/resume path | Child session/depth/revision/control details lost; no fleet/child controls or queue reclaim endpoint; resume failure starts a new session |
| Pi | Native session stream, steer, abort, command discovery, questions | Always submits busy input as steer; no follow-up API or extension-specific child/plan projection; native-input consumption matched by text |
| OpenCode | Session messages/tools, permissions and root abort | No child/todo inventory calls; unknown events discarded; role/thread controls not surfaced as common capability API |

### High-priority concrete findings

1. **Claude identity/history risk.** `tmux_interactive.py:1628–1632` treats
   `SubagentStart.transcript_path` as child history. Official hooks identify the
   common transcript as the **parent**; `SubagentStop.agent_transcript_path` is the
   child path. The stop handler at 1641–1644 omits that path and final response.
   This is a source/contract mismatch, not a reproduced billing incident ([C7]).
2. **Native tool evolution.** `tmux_interactive.py:1373–1381` specializes `Task`
   and `TodoWrite`, not current `Agent` and Task CRUD. Hooks alone do not restore
   missing task dependencies/ownership or complete child history ([C1][C5]).
3. **Resume identity.** `grok.py:665–679` uses `session/new` with a resume hint,
   despite advertising resume. `muse.py:944–958` catches resume errors and starts
   fresh. Existing passing tests can encode these undesirable behaviors.
4. **Event shape.** `grok.py:976–982` emits a plan inside system content, whereas
   `broker.py:3038–3039` updates the shared plan only on top-level `type:plan`.
5. **Recoverability.** Broker plan/fleet are in-memory projections, with bounded
   finished retention (`broker.py:586–595,2722–2755`). Same-process replay is not
   restart reconstruction. See the [earlier audit](codex-subagents-workflow-review-20260914.md)
   for the persistence and provenance paths.

Additional exact source ranges: Claude `tmux_interactive.py:1270–1290,1515–1668`;
Codex `codex_ws.py:904–934,1115–1140,2025–2087`; Grok
`grok.py:434–443,927–986,1118–1159`; Muse
`muse.py:1409–1422,1472–1479,1511–1522,1642–1668`; Pi
`pi.py:282–325,611–620`; OpenCode `opencode.py:296–329,547–580`.
All are relative to [`src/skuld`](../../src/skuld/).

## Proposed API contract

### Placement and discoverability

Use a versioned Skuld surface, proposed prefix **`/api/agent-runtime/v1`**.
Forge/NIUU exposes it through the existing host/session proxy
`/s/{forge_session_id}/api/agent-runtime/v1/...`. Preserve `(host, Forge session)`
as the routing context and native IDs inside resource references. Host-wide/project
aggregation belongs in NIUU/Forge, not inside an individual runtime process.

Publish an OpenAPI document and JSON schemas **inside that proxied prefix**. A
human frontend, CLI and an agent tool client use the same authenticated methods.
No UI-only privileged controls or private terminal-key rituals should be required
for a capability we claim to support semantically. Use existing identity/authorization
infrastructure, not a new auth scheme. An optional tool/MCP binding should be a thin
client over this API, with identical IDs, authorization and operation receipts.

The current generic proxy supports GET/POST/PUT/DELETE, not PATCH
([`session_proxy.py`](../../src/niuu/session_proxy.py):501–505). The proposal uses
those verbs deliberately. Streaming support and authorization must be validated
through the full host route, not only a broker-local URL.

### Read model

| Proposed endpoint | Purpose |
|---|---|
| `GET /schema` | Versioned machine-readable contract and typed native extension schemas |
| `GET /capabilities` | Effective runtime/version, enabled features, adapter support, qualification and limits |
| `GET /snapshot` | Consistent agent/plan/task/goal/submission/control snapshot plus replay cursor |
| `GET /agents?parent_id=&state=&cursor=` | Root/child/teammate instances; explicit hierarchy, paginated |
| `GET /agents/{id}` | Native identity, parent, role/objective, activity, last observation and per-target capabilities |
| `GET /agents/{id}/history?cursor=` | Public messages, tool activity, results and approvals for this target |
| `GET /agents/{id}/result` | Runtime outcome, artifacts and evidence; unavailable is distinct from empty result |
| `GET /plans?agent_id=&kind=` | Proposed-plan text or progress snapshot, source revision and native scope |
| `GET /tasks?agent_id=&owner_id=&state=` | Actual task objects where supported, with source identity and dependencies |
| `GET /goals?agent_id=` | Native objectives and lifecycle, separately from plans |
| `GET /workflow-runs?agent_id=` | Optional native workflow phases, budgets and child membership; unsupported explicitly where absent |
| `GET /submissions?agent_id=&state=` | Input admissions, queued work, consumption and disposition |
| `GET /operations/{id}` | Control outcome, acknowledgement level and target-state evidence |
| `GET /events?after=&agent_id=` | Cursor-based SSE or equivalent existing WS subscription; gap reported explicitly |

Agent history means inspectable native output, not hidden model reasoning. Do not
promise content that the runtime does not expose. Artifact access must use existing
authorized file access, not unrestricted native filesystem paths from untrusted input.

**Identity:** use opaque Skuld resource IDs mapped to
`{harness, runtime_instance_id, native_session_id, native_agent_id, native_thread_id}`.
Store parent relationship separately from `forked_from` and team membership.
Keep tool-call ID, attempt/turn ID and tmux pane ID as separate fields. Deriving
identity from a name, tool label, text content or pane position is insufficient.

**State:** separate lifecycle (`initializing|available|closed|unknown`), activity
(`idle|running|waiting|blocked|unknown`), last attempt outcome
(`completed|failed|cancelled|unknown`), and connection freshness
(`live|stale|disconnected|recovering`). Preserve native status verbatim. Completion
of one turn leaves a reusable agent available; disconnect is not proof of death.
Expose block reason and elapsed/last-activity timestamps, not fabricated percentages.

### Capability representation

Each operation needs more than a transport boolean:

```json
{
  "operation": "agent.steer",
  "native_support": "supported",
  "enabled": true,
  "adapter_support": "not_implemented",
  "control_path": "native_rpc",
  "target_eligible": false,
  "available_alternatives": ["parent_mediated_request"],
  "qualification": "read_only_observed",
  "reason": "Native child cannot accept direct input",
  "applies_at": "runtime_defined_boundary",
  "durability": "unverified"
}
```

This is an **illustrative proposed record**, not a current endpoint response.
Report unknown separately from unsupported. Include source runtime/schema version,
observation time, target identity, allowed delivery modes, byte/concurrency limits,
and whether a limit is native-enforced, gateway-enforced or merely advisory.
Do not report parent-mediated model requests as deterministic direct RPC controls.

### Mutations and operation receipts

| Proposed endpoint/action | Required semantics |
|---|---|
| `POST /agents/{id}/operations` → `spawn_child` | Native child only; objective, role, budget, parent identity. If only model-mediated, return that explicitly; do not silently create an unrelated Forge session |
| Same → `message`, `steer`, `follow_up` | Distinct intent and wake policy; target exact attempt when applicable; preserve text and provenance |
| Same → `interrupt`, `shutdown`, `close`, `resume` | Separate turn cancellation, graceful worker stop, history/handle closure and continuation; explicit descendant policy |
| `POST /submissions` | Stable request ID, target, input, `delivery_mode`, expected turn and native/gateway queue owner |
| `PUT /submissions/{id}` | Change only eligible queued input with expected revision |
| `DELETE /submissions/{id}` | Reclaim queued input; if already started, conflict—not implicit cancellation |
| `POST /submissions/reorder` | Exact queue/IDs/revision; only advertised where ordering semantics are qualified |
| `POST /tasks`; `PUT /tasks/{id}` | Native-backed task CRUD when exposed; creation/assignment/status change is not execution or evidence acceptance |
| `POST /plans/{id}/operations` | Request revision/implementation if supported; a read-only plan snapshot is not directly editable task data |
| `POST /goals/{id}/operations` | Native goal actions when available, retaining paused/blocked/limited states |
| `POST /workflow-runs/{id}/operations` | Advertised native pause/resume/stop actions, never implicit execution of a displayed checklist |
| `POST /controls/{id}/responses` | Answer the exact child approval/question; no promotion of agent text into user consent |
| `POST /native-operations` | Advertised, schema-validated namespaced operations for genuine runtime-specific features, not unrestricted JSON-RPC tunneling |

Every mutation receives a caller-stable `request_id` and returns an `operation_id`.
Track **admitted**, **delivered**, **observed effect**, **failed/rejected**, and
**indeterminate** separately. Receiving a tool response or HTTP 202 is not evidence
that the requested work finished. A child outcome can be completed while evidence
review remains `not_reviewed`; these are different records.

Use optimistic revision checks and typed errors: unsupported operation, feature
disabled, stale target/revision, not-owned target, already terminal, busy, saturated,
budget denied and delivery uncertainty. Stable rejection codes are better than a
silent control downgrade. Duplicate same-ID requests must return the original
receipt or conflict on changed payload. Equal text with different request IDs is
two different requests; do not content-deduplicate it.

Codex also has TUI-provided cross-task tools built on App Server thread operations,
separate from its nested child registry. Discover this as `native_cross_task_access`
with an explicit authorized scope; use typed namespaced operations for additional
client-specific actions. Do not overload checklist `/tasks` with conversation
management or assume these tools exist in a headless App Server connection.
See the [second-phase finding](codex-collaboration-workflows-20260914.md#cross-task-tools-are-another-native-client-surface).

### Native routing examples

| Intent | Codex | Claude/tmux | Grok | Muse | Pi |
|---|---|---|---|---|---|
| Read child | Native thread/history APIs | Hook identity plus supported child-history source | Child get/snapshot and qualified history surface | Child session read/view | Extension-specific handle/history |
| Steer child | Parent model route if direct input false | Native SendMessage or explicit team-pane route, identified as mediated/terminal | Feature-gated child-message extension with queue false | Child message/control or exact native steer where applicable | Extension route; root RPC steer is not universal child steer |
| Future input | Qualified thread queue | Not ordinary Enter; no strict future-objective mapping proved | Child queue extension semantics require qualification | Native queue disposition plus exact reclaim | Native follow_up, queue durability unverified |
| Stop attempt | Native turn or model child control as eligible | Agent task stop / TTY-specific control with proof | Child cancel outcome | Child interrupt or turn interrupt | Native abort on addressed process |

“After the current objective” is stronger than “after the current turn.” Unless the
runtime provides an objective-completion trigger, offer a pending intent with an
explicit release condition instead of promising automatic scheduling. Prefer native
queues where their semantics qualify. For unsupported durability, a future
**explicitly selected gateway-held queue** could persist intentions and wait for
release; it must not masquerade as a native queue or silently become a new scheduler.
No such new queue is implemented or required to expose today's read/control surface.

### Recovery, ownership and security

- Persist normalized snapshots/cursors with native provenance and sufficiently
  faithful events to reconstruct them. Never assume a broker in-memory dictionary
  is durable. Preserve empty-plan clear events and late result enrichment.
- After reconnect, reconcile with native state; report a gap or uncertain status
  when evidence is missing. Do not replay uncertain mutations automatically.
- Resume must retain identity or fail visibly. Replacement/new session requires
  explicit intent; never report a fresh runtime as a resumed child.
- Retain trustworthy `actor_kind`, authenticated actor ID, originating agent/session,
  input role, causation/request IDs and forwarding route separately. User echo is
  preserved even while queued; internal updates must never look user-authored.
- Per-target and project/session authorization bounds reads and controls. Do not
  grant a child broader data access simply because it can address the API.
- Cancellation declares `target_only|descendants` and queue policy. No implied kill
  cascade or history deletion. Graceful denial/timeout remains visible.
- Bound open agents, active attempts, pending input bytes/count, event buffers and
  model budgets. Admission backpressure precedes launch; token estimates are not
  hard enforcement unless the runtime actually enforces them.

## Implementation order and acceptance matrix

**P0 — Truthful read surface:** effective capabilities; identity/parent/attempt
mapping; plan/task/agent snapshots; public child history/results; cursor replay;
explicit unknown/stale states. Correct resume and proven attribution gaps in their
owned adapter lanes before relying on recovery.

**P1 — Safe control surface:** stable operations and input receipts, exact-target
steer, qualified child controls, queue/reclaim, questions and permissions. Expose
parent-mediated controls honestly. Keep native task mutation optional by capability.

**P2 — Shared UI and agent tools:** agent tree, selected-child transcript, plan/task
panel, pending-input panel, causal communication view, budgets and block reasons.
All consume the same API. Native extensions remain discoverable without forcing
new UI screens for every harness. This document does not claim every possible
runtime-specific action already has an adapter.

| Scenario | Required acceptance observation |
|---|---|
| Two children plus new queued user input | Both child identities/statuses visible; original input echoed once with queue identity; parent objective not silently redirected |
| Child inspect/steer eligibility | Read-only child can be opened; unavailable direct steer is explained; mediated request retains its status until actual native effect |
| Child fails, stalls or returns no evidence | Failed, waiting/stale and completed-without-reviewed-evidence remain distinct |
| Parent disconnect or process restart | No fabricated failure/success; history/plan/task/queue reconstruct or show explicit gaps; correct parent linkage retained |
| Native resume failure | No replacement conversation without explicit new-session instruction |
| Cancel during tools | Receipt then native terminal evidence; descendant and queued-input policies tested separately |
| Reclaim races queued launch | Either exact removal or already-started conflict, never silent cancellation |
| Duplicate delivery and late result | Stable same-ID receipt; result enriches existing object once; no child/tool duplication |
| Same text, distinct requests | Separate identities/echo/admission; no text-based dedup or cross-consumption |
| Task reorder or concurrent update | Revision conflict observable; priorities do not imply nonexistent native scheduling |
| Limit reached | Explicit saturation/budget rejection or queued admission; no orphan spawn and no hidden retry storm |
| Child approval/question and peer message | Correct originating child and requester; another agent cannot supply human authorization |
| New tool name or native status | Typed known projection or explicitly preserved extension/unknown—not dropped or mislabeled success |
| Plan mode under each harness | Permission/side-effect boundary verified; planning never falsely advertised as a universal read-only sandbox |
| Extension absent (Pi) | Capability says absent, not an empty fleet that implies supported-but-idle |
| Nested agents, fork and tmux split | Delegation parent, history fork and terminal pane represented as separate relations |
| HTTP/WS and UI/agent parity | Same target authorization, cursor, request IDs, schemas and effective limits on every access path |

Tests in this research phase: **91 existing mocked adapter tests passed**, no
warnings reported. The first attempt used an incomplete system Python environment
and failed collection on missing `asyncpg`; rerunning with the existing project
interpreter and this worktree's source succeeded without installing anything.
These tests establish existing projection behavior, not the proposed API, native
vendor guarantees, end-to-end recovery or a production release gate.

## Sources

All external sources accessed September 14, 2026. Links point to primary publishers
or their public code. Pinned code is more reproducible than mutable documentation;
neither is proof of an arbitrary installed runtime's enabled capabilities.

- **[C1]** Anthropic, [Claude Code subagents](https://code.claude.com/docs/en/subagents).
- **[C2]** Anthropic, [Agent teams](https://code.claude.com/docs/en/agent-teams).
- **[C3]** Anthropic, [Agent view and shell inventory](https://code.claude.com/docs/en/agent-view#manage-sessions-from-the-shell).
- **[C4]** Anthropic, [Cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging).
- **[C5]** Anthropic, [Structured task tracking and model availability](https://code.claude.com/docs/en/agent-sdk/todo-tracking).
- **[C6]** Anthropic, [Interactive queue and task list](https://code.claude.com/docs/en/interactive-mode#queue-messages-while-claude-works).
- **[C7]** Anthropic, [Subagent lifecycle hooks](https://code.claude.com/docs/en/hooks#subagentstart).
- **[O1]** OpenAI, [App Server](https://learn.chatgpt.com/docs/app-server), [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), and the [effective 0.154.0 local evidence](evidence/codex-native-agent-ui-20260914/native-read-results.json).
- **[G1]** xAI, [Grok subagents](https://docs.x.ai/build/features/subagents).
- **[G2]** xAI, [native task/child extensions, pinned source](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/extensions/task.rs).
- **[G3]** xAI, [feature-gated child messaging, pinned source](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/extensions/subagent_message.rs).
- **[G4]** xAI, [Plan Mode](https://docs.x.ai/build/features/plan-mode).
- **[G5]** xAI, [Headless sessions and ACP](https://docs.x.ai/build/cli/headless-scripting).
- **[G6]** xAI, [Workflows in Grok Build](https://x.ai/news/workflows), July 23, 2026.
- **[M1]** Meta, [MSP declarations, pinned source](https://github.com/meta-models/muse-code-sdk/blob/fbce769ccb75ab971d00e01a00fe076de4c773fc/schema/msp/msp.d.ts), especially child controls 1275–1325, snapshots 1240–1250, todo 1339–1359 and turns 1527–1611.
- **[M2]** Meta, [SDK/runtime changelog, pinned source](https://github.com/meta-models/muse-code-sdk/blob/fbce769ccb75ab971d00e01a00fe076de4c773fc/CHANGELOG.md#L195-L208).
- **[P1]** Pi maintainers, [Coding-agent README](https://github.com/earendil-works/pi/tree/main/packages/coding-agent).
- **[P2]** Pi maintainers, [RPC protocol](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md).
- **[P3]** Pi maintainers, [Subagent extension example](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/subagent/README.md).
- **[P4]** Pi maintainers, [Plan-mode extension example](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/plan-mode/README.md).
- **[OC1]** OpenCode, [Agents and roles](https://opencode.ai/docs/agents).
- **[OC2]** OpenCode, [HTTP server API](https://opencode.ai/docs/server).

Second phase: [Codex collaboration and long-session research](codex-collaboration-workflows-20260914.md).

[C1]: https://code.claude.com/docs/en/subagents
[C2]: https://code.claude.com/docs/en/agent-teams
[C3]: https://code.claude.com/docs/en/agent-view#manage-sessions-from-the-shell
[C4]: https://code.claude.com/docs/en/cross-session-messaging
[C5]: https://code.claude.com/docs/en/agent-sdk/todo-tracking
[C6]: https://code.claude.com/docs/en/interactive-mode#queue-messages-while-claude-works
[C7]: https://code.claude.com/docs/en/hooks#subagentstart
[O1]: https://learn.chatgpt.com/docs/app-server
[G1]: https://docs.x.ai/build/features/subagents
[G2]: https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/extensions/task.rs
[G3]: https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/extensions/subagent_message.rs
[G4]: https://docs.x.ai/build/features/plan-mode
[G5]: https://docs.x.ai/build/cli/headless-scripting
[G6]: https://x.ai/news/workflows
[M1]: https://github.com/meta-models/muse-code-sdk/blob/fbce769ccb75ab971d00e01a00fe076de4c773fc/schema/msp/msp.d.ts
[M2]: https://github.com/meta-models/muse-code-sdk/blob/fbce769ccb75ab971d00e01a00fe076de4c773fc/CHANGELOG.md#L195-L208
[P1]: https://github.com/earendil-works/pi/tree/main/packages/coding-agent
[P2]: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md
[P3]: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/subagent/README.md
[P4]: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/plan-mode/README.md
[OC1]: https://opencode.ai/docs/agents
[OC2]: https://opencode.ai/docs/server
