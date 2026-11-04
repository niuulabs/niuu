# Codex's existing subagents: what a frontend can inspect and control

**14 September 2026 — native Codex App Server API review.**

This answers the user's corrected objective: understand the agents **Codex already
creates and manages**, including proactive delegation, and expose that native
behavior in a better frontend. It does **not** propose another agent framework,
project scheduler or replacement workflow. It supersedes the earlier report's
workflow-design emphasis; its underlying source/test evidence remains useful.

## Direct answer

**Yes: Codex exposes native child identities, hierarchy, runtime status, history,
tool activity, plans and control primitives.** A frontend can provide an agent
tree with individual work views rather than hiding everything in the parent's
tool transcript.

**But inspecting a child and directly sending it user input are not the same
capability.** This audit directly queried the running App Server: both of our real
children are individually readable but return `canAcceptDirectInput: false`.
The parent returns `true`. Therefore “every child is an independently steerable
chat” would be wrong for the very session we are inspecting.

### What Codex's own interfaces offer

- **CLI:** `/agent` switches between active agent threads for inspection. The
  documented way to steer, stop or close children is to ask Codex to do it.
- **Desktop:** open a child from parent activity and inspect its work; ask Codex
  to steer/stop/close it.
- **IDE:** the background-agent panel, when available, can show status, open a
  thread and stop active subagents.
- **ChatGPT web:** Active/Done subagent lists are read-only; the documented sidebar
  is not a per-child steer/stop console.

These are existing client surfaces, not a claim that all clients expose identical
controls. [Official subagent controls](https://learn.chatgpt.com/docs/agent-configuration/subagents#managing-subagents).

Ultra's proactive delegation concerns **when Codex chooses to delegate**, not a
different agent-tree protocol. **Correction from the second-phase audit:** the
installed schema retains `MultiAgentMode` values `explicitRequestOnly`,
`proactive`, or custom instructions, but the **turn-start field is deprecated and
ignored**. Its description directs clients to `effort: "ultra"` for proactive
multi-agent behavior. The retained enum is not proof of an independently effective
switch. Plan/Default mode is still a separate concept. Verify effective version,
model-supported effort and applicable instructions rather than infer behavior from
a UI label. No mode settings were changed here. See the
[extracted effective contract](evidence/skuld-cross-harness-20260914/codex-mode-contract.json).

## 1. List the agents and identify which are running

Use **`thread/list`**, explicitly including spawned-agent sources. In the effective
0.154.0 schema, hierarchy filters are experimental:

```json
{
  "method": "thread/list",
  "id": 10,
  "params": {
    "parentThreadId": "01a09fd1-38e6-7b13-84c0-bbf0df249a47",
    "sourceKinds": ["subAgentThreadSpawn"],
    "useStateDbOnly": true,
    "limit": 25
  }
}
```

That is the actual parent ID of this review. Use `ancestorThreadId` instead of
`parentThreadId` for descendants at every depth; do not combine them. Paginate
using `nextCursor`. Normal default thread listing omits non-interactive sources,
so an ordinary session list can hide children even though the API supports them.

Relevant returned fields:

| Field | What the frontend gets |
|---|---|
| `id` | Stable native thread identity |
| `parentThreadId` | Native parent-child relationship |
| `source.subAgent.thread_spawn` | Parent ID, depth, agent path, nickname/role |
| `agentNickname`, `agentRole` | Human-facing identity when present |
| `status` | active, idle, notLoaded, systemError |
| `status.activeFlags` | waitingOnApproval / waitingOnUserInput when applicable |
| `canAcceptDirectInput` | true, false, or unavailable/null; experimental |

Use **`thread/status/changed`** for live status updates and **`thread/loaded/list`**
only to learn which IDs are resident in memory. **Loaded is not running.** An idle
child may have a completed task; inspect its turn/collaboration outcome rather than
labeling every idle thread “finished forever”.

### What the direct probe actually returned

| Thread | Identity | Runtime state | Direct user input |
|---|---|---|---|
| Parent | This review | active | true |
| Protocol child | Laplace / `/root/app_server_protocol` | idle; last turn completed | false |
| NIUU child | Gibbs / `/root/niuu_projection` | idle; last turn completed | false |

Both children were returned by the parent-filtered native list and by individual
reads. All three were loaded. The earlier demonstration captured both children
running concurrently; this later probe did not start new work.

Observed version caveat: this server's `thread/list` and `thread/read` returned
different `sessionId` values for the same children, while `id` and `parentThreadId`
agreed. Build the displayed tree from explicit child/parent IDs, not an assumption
that the listed `sessionId` always equals the root. This is an observed discrepancy,
not a new identity model to impose on Codex.

## 2. Open a child and inspect what it is doing

These are the same native thread APIs used for a parent conversation:

| Need | Native API/event | Notes |
|---|---|---|
| Metadata/current status | `thread/read {threadId,includeTurns:false}` | Does not resume or subscribe |
| Turn history | `thread/turns/list {threadId,itemsView,limit,cursor}` | Summary for overview; full for detail where supported |
| Messages/tools within a turn or thread | `thread/items/list {threadId,turnId?,limit,cursor}` | Returns `{turnId,item}` entries |
| Older full-history interface | `thread/read {threadId,includeTurns:true}` | Availability/size depend on history mode; prefer pagination where supported |
| Attach/rejoin for streaming | `thread/resume {threadId}` | Lifecycle/subscription operation, not a read-only list; avoid changing configuration merely to inspect |
| Stop receiving a thread's stream | `thread/unsubscribe {threadId}` | Unsubscription is not child cancellation |
| Public message/tool updates | `item/started`, `item/completed`, message/tool deltas | Route by native thread/turn/item ID |
| Turn outcome | `turn/completed` | completed/interrupted/failed and error information |

I successfully read both children's latest turns and paged one child's item history,
including message and command-execution items. **We can inspect child work through
the API, not just the parent's summary.** The probe retained only metadata/type
summaries, not full transcript bodies, in its published evidence.

For a frontend, expose a child detail pane with its own transcript, current tool,
last activity, question/approval and result. A single App Server connection may
carry events from several threads; keep the thread identity instead of folding
child events into the parent's conversation.

The [official App Server guide](https://learn.chatgpt.com/docs/app-server) documents
the thread/turn/item lifecycle, reads, resume and streaming. Its current prose
marks some pagination features experimental; this installed generated stable union
already includes the pagination methods. Capability/version checks matter.
No child resume/subscription or lifecycle change was performed by this probe.

## 3. Steering: two distinct native paths

### A. Parent-mediated management — already usable

The parent has native agent-management tools. The installed collaboration enum
includes spawn, message/input, follow-up task, wait/list, interrupt, resume and
close operations. The actual model tool surface is a subset; in this session it
includes `send_message`, `followup_task`, `list_agents`, `wait_agent` and
`interrupt_agent`, but does not expose every enum operation.

For our actual surface:

- `send_message`: communicate with the child; does not itself start a new turn.
- `followup_task`: delivers an assignment; triggers a turn if the child is idle.
- `interrupt_agent`: interrupts current work while leaving the child addressable.
- `list_agents` / `wait_agent`: inspect status and wait for updates/results.

The existing user interaction is: ask the parent to redirect a named child; Codex
uses these native controls. That also preserves the parent's awareness of the
change. A frontend can offer **“Ask parent to steer this agent”**, targeted using
the real child identity, without inventing a new agent runtime. It must show this
as a request until Codex has actually acted, not a deterministic control ACK.

There is **no standalone App Server client RPC named `agent/sendMessage` or
`spawn_agent`** in the generated client-request union. Agent tools exposed to the
model are not automatically RPCs exposed to a frontend.

### B. Direct thread input — capability-gated

For a thread reporting `canAcceptDirectInput: true`:

- **Active turn:** `turn/steer {threadId,expectedTurnId,input}` adds input to that
  turn. The expected ID protects against steering the wrong turn.
- **Idle thread:** `turn/start {threadId,input}` begins a new turn. That is a
  follow-up request, not steering an already-running turn.
- **Cancel a turn:** `turn/interrupt {threadId,turnId}` is the native cancellation
  request. Wait for the resulting turn status; a request ACK alone is not stop proof.

`canAcceptDirectInput: false` on **our two children** means the frontend must not
offer their direct-input composer as though it worked. Null means unknown/unavailable,
not true. I did not send a deliberately invalid steer, restart a child, or test
cancellation on active work. Direct cancellation authorization/behavior for a child
was not established by the input flag alone.

This is the main limit on treating Codex like an unrestricted team-chat console:
**viewable child thread does not imply direct user-steerable child thread**.

## 4. Plans and tasks: what state can actually be lifted into the UI?

### Plan mode exists

The live **`collaborationMode/list`** response contained **Plan** and **Default**.
The installed `ModeKind` enum is `plan | default`; there is no third `task` mode
in that enum. `collaborationMode` is the protocol's planning/execution-mode setting,
despite its name—it is not synonymous with multi-agent/team mode.

An opted-in client selects it on `turn/start` using:

```text
collaborationMode: {
  mode: "plan",
  settings: {
    model: selectedModel,
    reasoning_effort: selectedEffort,
    developer_instructions: null
  }
}
```

This is a schema illustration, not a turn executed by this review. Null developer
instructions select Codex's built-in mode instructions. Fetch available presets
rather than assuming a frontend label's defaults. This setting is experimental in
the effective schema. A proposed plan can be emitted as a native `plan` text item;
the completed item is authoritative over incremental `item/plan/delta` text.

### The live progress checklist also exists—and is separate

Subscribe to **`turn/plan/updated`**:

```text
{
  threadId,
  turnId,
  explanation,
  plan: [
    { step: "Inspect the protocol", status: "completed" },
    { step: "Check the implementation", status: "inProgress" },
    { step: "Summarize findings", status: "pending" }
  ]
}
```

These are native structured fields, **not Markdown scraping**. A frontend can show
the parent's checklist and a selected child's checklist if that child emits one.
Keep the proposed plan document separate from the progress snapshot; neither means
all children share one machine-readable task board.

Important native limits:

- The progress snapshot has step text/status, not stable task IDs, assignee IDs,
  dependencies or a fleet-wide execution graph.
- No native `plan/get` or user-facing `plan/update` client RPC appears in this
  version's request union. The live notification is the structured plan source;
  preserve it through existing session replay if the frontend must reopen it.
- A child may produce no plan. Show “No plan published”, not an invented checklist.
- A list of tasks in the user's prompt is not automatically an API task registry.
  Codex's parent decides how to split work and follows results through native
  collaboration activity. Show explicit task/agent links only where actual events
  provide them; do not pretend the plan declares assignments it does not contain.

### Other native state, not substitutes for the plan

- `thread/queue/*`: per-thread queued future **inputs**. The live queue-list RPC
  worked and was empty here. It is not a global list of assigned subagent tasks.
- `thread/goal/*`: a persisted per-thread objective/lifecycle. Goal-get worked and
  returned no goal here. It is not the parent's progress checklist.

This review does not propose replacing either feature or adding a project queue.

## 5. What a better frontend can expose without replacing Codex's workflow

| Frontend surface | Existing Codex source |
|---|---|
| Agent tree / Active / Waiting / Done | Filtered native threads, parent IDs, runtime status and collaboration/turn outcomes |
| Click an agent to inspect its work | Native child turns/items plus subscriptions for live events |
| Parent plan and selected-child plan | `turn/plan/updated`, keyed by thread and turn |
| Plan/Default mode selector | `collaborationMode/list` + `turn/start.collaborationMode` |
| Direct child composer when supported | `canAcceptDirectInput` + turn/start or turn/steer |
| Parent-mediated steer action otherwise | A user request to the parent to use its native child controls, with observed acknowledgement/result |
| Stop, approval and question controls | Native turn/control methods, preserving exact owner IDs and capability limits |
| Future input list, if desired | Native thread queue, clearly distinct from agent task status |

That is primarily **a richer view and control surface over Codex's existing state**.
It does not require choosing among different subagent implementations.

The integration gap relevant to Lexi is narrower than my first report suggested:
reviewed Skuld already exposes a basic fleet, but binds normal input to the parent,
reduces child notifications to generic `agent_event`, ignores some status updates,
and has no general child-thread history/control facade. Preserve/expose native
thread APIs and events instead of reconstructing an imaginary team task system.
This is a frontend/API mapping, not authorization to implement or deploy it now.

## Evidence and limits

- Effective own running executable rechecked: App Server `0.154.0`; no PATH inference.
- [Live read results](evidence/codex-native-agent-ui-20260914/native-read-results.json):
  11 successful read-only RPCs after initialization, own parent/two existing children
  only. Connection closed afterwards; no thread subscriptions or model turns.
- [Schema classifications](evidence/codex-native-agent-ui-20260914/schema-classification.json):
  exact selected types plus stable/experimental differences. See earlier
  [effective schema evidence](evidence/codex-subagents-20260914/protocol-extract.json).
- Initial diagnostic connection failed before handshake. Matching the existing
  Skuld client's explicit Unix-WebSocket URI and `compression=None` succeeded.
  No service/config changes; no claim that compression alone was isolated as cause.
- No new children launched; original authorization of two total is respected.
  No live steer/cancel/resume or shared-service change. UI/device rendering remains
  untested. The 196 offline tests belong to the earlier review, not this new probe.
- Code references at original source base `21bb0de6`: transport events/filtering
  `src/skuld/transports/codex_ws.py:904–970,1115–1140,1208–1216,1807–1818,2025–2087`;
  parent controls `2506–2563`; basic fleet read `src/skuld/broker_api.py:544–551`.

**Recommended next conversation:** agree which native surfaces should be visible
in Lexi—agent tree, child work view, plans, and capability-aware steering—using
these existing APIs. Not another workflow-design exercise.
