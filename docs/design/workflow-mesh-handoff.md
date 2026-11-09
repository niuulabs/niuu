# Workflow step handoffs over the mesh

Status: **design approved for build, revision 6**, nothing built. 2026-11-09. Revision 5 added
"one run, one unit" (section 2) and the build plan (section 11); revision 6 records the user's
three decisions (section 12).

## The decision

A workflow step is a message published on the run's mesh on a topic. The members whose
persona declares that topic receive it and do the work, in parallel; the publisher does not
care how many. Outcomes and gates come back as events in the run's thread. The next step is
decided per workflow by Ting's **engine** or by a **coordinator**. Every run has a **root**,
which holds the run's room and thread and is the parent of every member. **A run is one unit
of compute**: one pod, one VM or one VM slot hosts the root and every member, all started
together at launch, and the mesh is local inside it. Skuld is the only bridge: each Skuld
reports what it did on the run's mesh to Forge as session activity, and Ting records the run
(steps, gates, endings) from Forge's activity stream, as it does today. Ting never joins the
mesh and never relays between agents; Forge passes nothing between agents. Today's HTTP step
delivery is **removed in the same work**. No new screens.

## Today

Ting creates the run's room session (`wf-<slug>`, a Skuld with no agent, `workflow_run_room`)
and then one session per member, each its own pod, created lazily when the member's first
step activates (`ting/adapters/forge_step_sessions.py`). On the live runs the reviewer
started minutes after the coder. The engine (`ting/domain/step_engine.py`) decides the next
activations; `ForgeStepSessions.deliver` sends each, per member, to
`POST /sessions/{id}/workflow/steps`, which Forge relays to the member's Skuld
(`skuld/workflow_steps.py`). The harness closes it with `workflow_step_close`; Skuld reports
the close as activity and Ting applies it, fenced on activation, attempt and session. Members
share their replies, tool work and closes on the run's mesh (`skuld/shared_work.py`), and the
room stores them. Ting posts step, gate and end notices to the room through the room's
`notice` operation (`ting/domain/services/run_room_notices.py`). The session-native gate path
was retired (`1e98704f1`); gates live in Ting.

## 1. The run root

Each run has one root: **a Skuld with no agent or harness** (live on dev.589). It hosts the
run's room and is the parent of every member session. Members publish everything onto the
run's mesh and the root stores it, so opening the root shows the run as one session.

| | What the root is in it |
|---|---|
| Room and thread | where they live, in both modes |
| Engine mode | the session that carries Ting's offers onto the mesh |
| Session mode | the coordinator: the same root, with a coordinator agent running in it |
| Compute | the unit every member runs in (section 2) |

## 2. One run, one unit

### What it means

- **One unit per run**: the root's Skuld and every member's Skuld and harness run in the same
  pod, VM, VM slot, sandbox or local process group.
- **Each member stays its own Forge session**: its own transcript, durable log, credential,
  activity, outcome and mesh peer id, nested under the root. Only the compute is shared.
- **All members start at launch**, with the root, not when their first step is offered. The
  members of a run are fixed when the unit starts.
- **The mesh is local inside the unit**: nng on `127.0.0.1` (the OS picks each port) with
  `registry` discovery on a file in a directory the unit's Skulds share. Nothing outside the
  unit can reach a mesh port, and a run needs no cross-machine discovery.

### One Skuld per member, not one Skuld for several sessions

The old flock pod (`RavnFlockContributor`, removed in `718e35694`) ran one Skuld beside N Ravn
daemon containers in one pod: a container per persona, ports by index (`pub`, `rep`, handshake,
gateway), its config written by an init container, the workspace shared by `subPath`, a
static mesh peer list, and on OpenShell the same personas as peer workloads sharing
`/tmp/niuu-mesh`. The agents were not Forge sessions; they were participants of the one
session's room.

Here each member must be a Forge session, and a Skuld *is* one session: its session id is its
mesh peer id, its `forge_session` credential is bound to that one session and launch, its
durable event log, activity reports, transcript, step host and harness process are all one
session's. A Skuld hosting several sessions would rework every one of those, and one member's
harness failure would take the others with it. So **one Skuld per member**, each running its
own harness, plus the root's agentless Skuld. What the flock pod gives us to reuse:

- containers beside the main one: `PodSpecAdditions.extra_containers`, rendered by the skuld
  chart's `extraContainers` (both still exist);
- ports by member index (the flock's port scheme) for each member's broker, which Forge's proxy
  addresses;
- a shared `emptyDir` for the local mesh (the flock's `/tmp/niuu-mesh`) and the run's
  workspace by `subPath`;
- on OpenShell, members as peer workloads (`openshell_contract.check_peer_workloads`, which
  still exists).

Not reused: the Ravn sidecar runtime, the init-container config writer (Skuld reads its
config from the values the chart renders), and static peer lists (the registry discovers).

### In Forge

- A member session is created **hosted by** the run's root (`hosted_by = <root id>`, a column
  on the session). Ting creates the root and every member, then starts the root; Forge starts
  the unit with all of them. A member created for a root whose unit is already running is
  refused (409, "a run's members are fixed when its unit starts").
- A hosted member has no pod, VM or sandbox of its own. Its status comes from its own process
  in the unit (Kubernetes: its container's status; elsewhere its process), and its broker
  endpoint is the unit's address plus its member port, so every Forge route that reaches a
  session (chat, log, files, room, offers) works unchanged.
- Each member's credential reaches only its own Skuld: on Kubernetes its own Secret mounted
  into its own container; elsewhere a file only its process can read (below).
- The unit's resources are the sum of its members' and the root's. A unit that does not fit
  where it is scheduled fails to start and says so; nothing is shrunk.
- Stopping the root stops the unit and every member. Stopping one member stops its Skuld and
  harness, not the unit. Ting's `replace_session` (reassign) restarts the member's harness in
  place under a new launch of the same session, because a running unit cannot take a new
  member.

### Each runtime

| Runtime | The unit | Members are | Shared mesh directory | A member's credential |
|---|---|---|---|---|
| Kubernetes, Flux (valhalla) | one pod: one HelmRelease, the root's | containers (`extraContainers`), one per member, ports by index | an `emptyDir` mounted into every container | its own Secret (`sessionCredentialProjection`), mounted into its container only |
| Kubernetes, direct pod manager | one pod | containers, as Flux | as Flux | as Flux |
| VM, one session per VM (container runtime) | one VM | containers on the VM, one per member | a directory bind-mounted into every container | its own file, mounted into its container only |
| VM slot | one slot | processes in the slot, one per member, all as the slot's one Unix user | a directory in the slot | its own file in the slot; readable by the run's other members (decision 2) |
| OpenShell (noatun) | one sandbox | peer workloads, one per member; the root is the primary | `/tmp/niuu-mesh` (the fork's shared `emptyDir`) | its own file in the sandbox's credential files, per member |
| Mini mode | one local process group | processes, started and stopped together | the registry file beside the workspaces (exists) | its own file (exists) |

OpenShell's fork admits at most 16 peer workloads per sandbox; that is the real bound on a
run's members there, and a larger run is refused at launch naming it.

### Secret injection on Kubernetes

An injector that works on the pod (the OpenBao agent injector, Infisical's: annotations plus a
ServiceAccount per session, `SecretInjectionPort.injects_per_pod`) gives a pod one injection.
A member is a container of the root's pod, so the injection is the root's and a member adds no
pod-level part (a ServiceAccount, annotations, init containers), which the Flux and direct
pod managers refuse. Forge keeps the members' needs met in three places:

- the root's pipeline sees each member (`SessionContext.unit_members`, resolved exactly as the
  member's own launch resolves its integrations) and the injection renders the union of
  their credentials; all members share the root's owner, so one role reading the union
  grants nothing a member lacked. A credential several members need is rendered once; two
  members wanting one env var or file from different credentials is refused at launch;
- the injector is told to inject the members' containers too (`agent-inject-containers`),
  so they read the rendered `/run/secrets`;
- the pod runs as the root's ServiceAccount, so the room-role route
  (`participants/role`) accepts that pod's workload credential for the root and for the
  sessions it hosts, and for no other session.

A VM unit keeps an agent per member (explicit containers on the guest); the root's injection
there covers only its own credentials.

### Workspace

The workspace is shared wherever the runtime allows it (decision 1). The flock shared one
workspace across its personas, and a run's members do the same work on the same repository,
so the unit has one checkout of the run's repository and branch, shared by every member, and
each member keeps its own home. The reviewer reads the coder's working tree; nothing has to
be pushed between steps. A runtime that cannot share a directory between its members says so
at launch rather than giving each member a copy silently; every runtime in the table can.

## 3. What travels on the mesh

**Agents talk freely.** `mesh_publish`, `mesh_ask`, messages, rooms and every declared topic
stay as they are. Nothing here limits what agents say or which topics they use.

**Ting records a small, open set of workflow events.** They are one mesh event type,
`workflow`, with a `kind`. Only Skuld makes them, when an agent calls `workflow_step_close` or
a coordinator tool, so each is stamped and signed by the platform. An event of a kind Ting
does not know is carried, reported and shown in the room, and Ting records nothing for it.

| Kind | What it means | Who sends it |
|---|---|---|
| `step.offered` | "Here is work for whoever does this kind of work": node, task, inputs, deadline, how to close it | the root: for Ting's engine, or for its coordinator agent |
| `step.picked_up` | "I am doing it." | each member that takes the offer |
| `step.passed` | "I received it, and it is not mine": the node's filters for this member do not match | a member that received the offer and did not take it |
| `step.outcome` | "Done, and this is how it ended": verdict, outcome, note, evidence, or waiting/failed with the reason | the member, on `workflow_step_close` |
| `gate.opened` | "A person must decide before the run goes on." | the root |
| `gate.decided` | "Approved, or changes requested, by whom." | the root |
| `run.ended` | "The run is over: completed, failed or canceled, and why." | the root |

**Topics.** Every workflow event carries `correlation_id = run id`, so it is on the run's
thread, which the root follows and stores and its room shows. An offer also goes on the topic
the node's members consume (e.g. `code.requested`); an outcome also goes on the topic it
produces (e.g. `review.passed`), as information: work starts only from an offer. Event ids
derive from the offer id or activation id, so a republish is the same event.

**Who takes an offer.** Only a session whose pinned persona declares the topic in `consumes`.
A session that follows the topic at runtime (`mesh_subscribe`) sees the offer and does not
take it.

## 4. The contract travels with the offer

The offer carries what a session is told today: valid endings and verdicts, verdict → event
map, outcome schema, deadline, inputs, instructions, generation and the persona pin. Verdicts,
the verdict map and the outcome schema are a function of the pinned persona
(`persona_contract_of`, moved from `ting/domain/step_graph.py` to `niuu.domain.workflow_step`).
The member's Skuld derives them from its own pin and refuses an offer that differs, naming
both. A stage member's `eventFilters` reach its session at launch and decide `picked_up` or
`passed`. `workflow_step_close` does not change for the agent; after the close Skuld writes the
frame, reports `closed` to Forge and publishes `step.outcome`.

## 5. Engine mode

1. At launch Ting creates the root and every member, hosted by the root, and starts the root:
   the unit comes up with all of them. Each member reports `listening` once its subscriptions
   are up.
2. The engine decides as now; the activations one event produces for one node become one
   offer.
3. Once every member whose persona consumes the offer's topic is listening, Ting signs the
   offer (`service:ting`) and posts it to the root through the room route Ting already uses
   for notices, as a new operation `offer` (`POST /sessions/{root}/room/offer`; Forge proxies
   it; `forge_session` tokens refused; owner-only at the broker). One call per offer. The root
   publishes it verbatim.
4. Members check the signature (an engine run accepts only Ting's offers), the pin and their
   filters, hold the step durably, report `picked_up` or `passed`, and start it at the next
   turn boundary. Each pickup is its own step in the ledger, fenced on its session.
5. The offer's outcome is in when every member that picked it up has closed; the engine counts
   those members for its joins, and the next offer follows.

## 6. Gates

Gates stay in Ting in both modes, as today: Ting's engine holds them, raises the notification
on the root, and a person decides on the run's review page through Ting's gate route. A
person's gate refuses agents unless it is `automated_approval`. On the mesh the root publishes
`gate.opened` and `gate.decided`, which Ting posts to it with the room's `notice` operation, as
`RunRoomNotifier` does now. In session mode the coordinator asks for a gate with a tool; its
Skuld reports `gate_requested`, Ting opens the gate in its ledger the same way, and the
decision reaches the coordinator as a notice at its turn boundary.

## 7. Session mode

The workflow document gets an optional `orchestration` key: `mode: engine | session`
(default `engine`) and the coordinator's persona: the **built-in coordinator persona** by
default, or a pinned one (a Ravn resident later). In session mode the coordinator is the run
root itself, with that persona's agent running in it. Members accept only the root's offers.

Coordinator tools (built-in Forge MCP server, coordinator only): `workflow_step_offer`
(refused with the reason when no member consumes the topic), `workflow_gate_request`,
`workflow_run_end`, `workflow_run_state`. Pickups, outcomes and gate decisions reach it as
notices. What comes next is its judgment. Ting records and does not decide; an offer outside
the pinned graph is recorded and shown, not refused. A coordinator works with the members the
run launched with; it cannot add members (decision 3).

## 8. What Skuld reports, and how Ting records it

Each Skuld reports **only its own acts**, on the ordered, acknowledged activity queue it has
today; Ting attributes every report by the session of the authenticated envelope.

| Report (`metadata.workflow_step.event`) | Sent by | Dedup key |
|---|---|---|
| `listening` (new) | every member, after each start | session + start |
| `offer_published` (new) | the root | offer id |
| `picked_up`, `passed` (new) | the member | offer id + session |
| `offer_refused` (new) | any receiver; error log and counter | offer id + session |
| `closed`, `unclosed` | the member, as today | `is_repeat_close`, turn, as today |
| `gate_requested`, `run_ended` (new) | the root, in session mode | gate id, run |

A repeat changes nothing, which covers the duplicate activity of today's per-owner streams.
After a stream reconnect Ting reads the `workflow_step` frames from Forge's durable session
log (`GET /sessions/{id}/log`). A migration in `migrations/ting/` adds `offer_id`,
`picked_up_at` and `orchestration`, and drops `delivered_at`.

**Nobody picks it up.** No member listening yet: publication waits, and at the step deadline
the step fails naming `workflow_steps.step_deadline_seconds`. Published and no pickup within
`workflow_steps.pickup_grace_seconds`: flagged `unclaimed`, a person is told once, and the same
event is republished, counted; the grace is when to look again, not a cap. An offer every
member passed is an answer and is not republished. Overdue, unclosed and session gone stay as
today; a root that is gone is flagged and the run waits for a person.

## 9. Why OpenShell, VM and host sessions had no mesh

The run's mesh settings went in `values.mesh`, which only the Flux runtime reads, and since
#1293 only the Kubernetes and local pod managers give nng a discovery, so Skuld refused to
start the mesh elsewhere. With one unit per run every runtime uses the same local shape (nng
on loopback, `registry` on the unit's shared directory), so every pod manager only has to
render the unit and carry the run's mesh settings into each member's Skuld.

## 10. Migration

The work lands in the order of the build plan. When the handoff is deployed, runs still open
are canceled with the reason `canceled at upgrade: step handoff moved to the mesh`, by
`WorkflowRuns.cancel_runs_open_before_mesh` at Ting's start.

**Deleted:**
- **Ting:** `StepSessionPort.deliver` and `observe`, `SessionNotReadyError`,
  `ForgeStepSessions.deliver` and `activation_body`, the engine's `_deliver_or_defer`,
  `_deliver`, `_defer_delivery`, `_deliver_when_up` and `resume_deliveries`,
  `claim_delivery` / `release_delivery` / `undelivered_steps`,
  `VolundrPort.deliver_workflow_step` / `get_workflow_step` and their HTTP adapter, the
  `undelivered` and `not_started` stuck conditions and the re-delivery action,
  `workflow_steps.session_poll_seconds`, and the step notices `RunRoomNotifier` posts (the
  mesh events replace them; gate and end notices stay).
- **Forge:** `deliver_workflow_step`, `get_workflow_step`, `_proxy_workflow_step_call`
  (`rest.py`) and the gateway relays in `niuu/adapters/inbound/rest_volundr.py`.
- **Skuld:** `POST /api/workflow/steps`, `GET /api/workflow/steps/{id}`, and
  `WorkflowStepRequest` as an HTTP body.
- **Tests:** `test_adapters/test_rest_workflow_steps.py`, the step routes in
  `test_niuu/test_rest_volundr.py`, the HTTP cases in `test_skuld/test_workflow_steps.py`, and
  delivery in `test_ting/` (`test_forge_step_sessions.py`, `test_volundr_http.py`,
  `test_postgres_workflow_steps.py`, `test_ports.py`, `test_step_engine.py`, `fake_forge.py`,
  `step_support.py`, `stuck_support.py`, `workflow_step_contract.py`).
- **Docs:** the delivery sections of `docs/operator/workflow-step-sessions.md`.

## 11. Build plan

Each slice is one engineer in one worktree, branched from `dev`, merged back with its tests
green. "Owns" lists the files it may change; no two slices that run at the same time own the
same file. Every live check runs a small workflow (coder → reviewer → gate → end, one Claude
Code and one Codex member) in the dummy project and ends with the sessions stopped.

**Wave 1, in parallel: foundations**

| Slice | Builds | Owns | Depends on | Proof |
|---|---|---|---|---|
| **A. Shared shapes** | the `workflow` event shape and kinds, the offer, `MeshEventType.WORKFLOW`, `persona_contract_of` and the close how-to moved to niuu (Ting keeps importing them) | `src/niuu/domain/workflow_step.py`, `src/niuu/mesh/events.py`, `src/ting/domain/step_graph.py` (the moved function only), `src/ting/domain/step_instructions.py`, their tests | — | unit tests for every kind's shape, unknown kinds kept, contract derivation equal to today's for the bundled personas |
| **B. Hosted members in Forge** | `hosted_by` on sessions, create validation (members only before the unit starts, 409 after), member status from the unit, member endpoint = unit address + member port, start/stop of the unit with its members | `src/volundr/domain/models.py` (Session), `src/volundr/domain/services/forge.py`, the session repository and a migration in `migrations/`, the create body in `src/volundr/adapters/inbound/rest.py` (`SessionCreate` only), their tests | — | service and REST tests: create, refuse after start, status, endpoint, stop cascade |

**Wave 2, in parallel: one unit per runtime** (each after B)

| Slice | Builds | Owns | Depends on | Proof |
|---|---|---|---|---|
| **C. Flux unit** | the root's HelmRelease renders one container per member (its own Skuld config, Secret, port by index), the shared mesh `emptyDir`, the shared checkout; registry discovery inside the pod | `src/volundr/adapters/outbound/flux.py`, `src/volundr/adapters/outbound/execution_credential_k8s.py`, `charts/skuld/` (templates, values, README), `tests/test_charts/test_skuld_chart.py`, flux tests | B | chart and flux tests; **valhalla**: a run's coder and reviewer start together in one pod, `mesh_peers` in each shows the other on loopback, each member's page shows its own transcript |
| **D. Direct Kubernetes unit** | the same with the direct pod manager | `src/volundr/adapters/outbound/direct_k8s_pod_manager.py`, its tests | B | pod-spec tests (a cluster on the direct manager, if one is in use) |
| **E. VM unit** | one VM (container runtime) and one VM slot hosting every member; the shared mesh directory; per-member credential files; guest metadata answering per member session (decision 2) | `src/volundr/adapters/outbound/vm_pod_manager.py`, `ssh_vm_runtime.py`, `ssh_host_vm_runtime.py`, `vm_slot_supervisor.py`, `vm_slot_layout.py`, `src/volundr/domain/services/guest_metadata.py`, `rest_compute_metadata.py`, `.claude/rules/architecture.md` (the guest metadata section), their tests | B | runtime and supervisor tests; **Valhalla VMs**: both members on one VM, mesh on loopback; **Valhalla VMs with slots**: both in one slot, the other slot untouched |
| **F. OpenShell unit** | the root as the sandbox's primary and each member as a peer workload, per-member credential file, `/tmp/niuu-mesh`, the 16-peer refusal at launch | `src/volundr/adapters/outbound/openshell_gateway.py`, `openshell_contract.py`, their tests | B | gateway tests; **noatun**: both members in one sandbox, mesh on loopback |
| **G. Mini mode unit** | members as processes of one group with the root | `src/volundr/adapters/outbound/local_process.py`, its tests | B | local tests; `niuu platform up` run on a laptop |

**Wave 2, in parallel with the units: the handoff**

| Slice | Builds | Owns | Depends on | Proof |
|---|---|---|---|---|
| **H. Skuld handoff** | offers from the mesh (signature, pin, contract, filters), `picked_up` / `passed`, one step at a time, `step.outcome` on close, `listening`, the root's `room/offer` operation, the new reports; the step HTTP routes deleted | `src/skuld/workflow_steps.py`, `mesh_adapter.py`, `mesh_topics.py`, `activity_reporting.py`, `broker_api.py`, `config.py`, `src/niuu/mesh/platform_events.py`, their tests | A | tests for each refusal (bad signature, other orchestrator, pin, contract), pickup, pass, outcome, repeat offer, restart restoring held steps |
| **I. Ting ↔ Forge** | create the root and every member hosted by it at launch, start the root; the `room/offer` call; `replace_session` as an in-place member restart | `src/ting/adapters/forge_step_sessions.py`, `src/ting/ports/volundr.py`, `src/ting/adapters/volundr_http.py`, their tests and `tests/test_ting/fake_forge.py` | A, B | adapter tests against the fake Forge |
| **J. Ting engine over the mesh** | offers from the engine's activations, publish when listening, pickups as steps, joins over pickups, `unclaimed` and republish, the ledger migration, the subscriber's new reports, catch-up from the log; Ting's delivery code deleted | `src/ting/domain/step_engine.py`, `domain/services/workflow_step_engine.py`, `domain/services/activity_subscriber.py`, `domain/step_stuck.py`, `domain/services/workflow_step_watch.py`, `ports/workflow_steps.py`, `adapters/postgres_workflow_steps.py`, `migrations/ting/`, `domain/services/run_room_notices.py`, `src/ting/config.py`, their tests and `step_support.py`, `stuck_support.py`, `workflow_step_contract.py` | A, I | engine tests (fan-out to two consumers from one offer, a pass, a join over pickups, rework, a repeated report), stuck tests |
| **K. Room display** | `workflow` events in the root's room as one line each, unknown kinds included | `src/niuu/collaboration/mesh.py`, the web room renderer it feeds, their tests | A | tests; on valhalla the root's window shows offer, pickups, outcomes, gate |

**Wave 3, after C, H, I and J are merged: removal and the end-to-end proof**

| Slice | Builds | Owns | Depends on | Proof |
|---|---|---|---|---|
| **L. HTTP step delivery removed** | Forge's step routes and proxy, the gateway relays, the remaining tests and docs of the HTTP path; cancel open runs at upgrade with the reason | `src/volundr/adapters/inbound/rest.py` (the step routes), `src/niuu/adapters/inbound/rest_volundr.py`, `tests/test_adapters/test_rest_workflow_steps.py`, `tests/test_niuu/test_rest_volundr.py`, `docs/operator/workflow-step-sessions.md`, `docs/operator/agent-messaging.md`, `docs/operator/limits.md`, the upgrade cancel in `src/ting/domain/services/workflow_runs.py` | H, J (and B for `rest.py`) | grep finds no `workflow/steps` route; **valhalla end to end**: launch, both members up together, offer → two pickups → closes → gate approved → run completed, then the same on Valhalla VMs and noatun as their slices land |

**Wave 4: session mode** (after L is live)

| Slice | Builds | Owns | Depends on | Proof |
|---|---|---|---|---|
| **M. Coordinator** | `orchestration` in the workflow document, the built-in coordinator persona, the coordinator agent in the root, its tools, `gate_requested`, the session-mode recorder in Ting | `src/ting/domain/workflow_document.py`, `src/personas/builtin/`, `src/niuu/forge_mcp/`, `src/skuld/forge_mcp/`, a new `src/ting/domain/services/run_recorder.py`, their tests | L | tests; **valhalla**: a run whose coordinator offers code, then review, asks for the gate and ends the run |

Parallel at a glance: A ∥ B; then C ∥ D ∥ E ∥ F ∥ G ∥ H ∥ K, with I after A and B, and J after
I; L after H and J; M last. C is the critical path for the first live proof; E, F and G can land
after it.

## 12. Decided by the user (2026-11-09)

1. **The workspace is shared wherever possible**: one checkout of the run's repository and
   branch in the unit, shared by every member; each member keeps its own home (section 2).
2. **Members in one VM slot run as the slot's one Unix user.** They can read each other's
   files, credentials included; that is accepted for now, within one run of one owner. A
   member per sub-user is future work. Slice E still changes the guest-metadata rule in
   `architecture.md` ("answers only for the session currently bound to one slot"): it now
   answers per member session of the slot, and the rule's text says members of one run share
   the slot's user.
3. **Session mode adds no members.** A coordinator works with the members the run launched
   with; `workflow_member_add` is not built, and slice M does not include it.

## Future work

- A mesh between units: the `forge` discovery (Forge certifies each session's address and lists
  its realm's peers) and per-slot port forwarding, for members that run in different units.
- Hosts behind NAT, which no peer can dial.
- An offer taken by exactly one of several sessions (a work queue).
- One session spread over several slots or pods.
- A Unix user per member inside a VM slot, so members cannot read each other's credentials.
- A coordinator adding members to a running run (a restart of the unit, or a unit of its own
  joined by the mesh between units).
