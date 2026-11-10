# Workflow steps on harness sessions

Ting runs a workflow as a **run**: a campaign plus a step ledger. Every stage member of
the workflow is a persona, and each persona runs on an ordinary harness session (Claude
Code, Codex and the others) that Forge creates. Ting never relays conversation between
agents; it offers steps on the run's mesh and records how they end.

## One run, one unit

A run is **one unit of compute**: one pod, VM, VM slot, OpenShell sandbox or local process
group hosts the run's root and every stage member. They all start together when the run
launches, and a run's members are fixed from then on.

- **The root** is a Skuld with no agent. It holds the run's room and thread and carries
  Ting's offers onto the run's mesh. It is named `wf-{run slug}`.
- **Each member** is its own Forge session, hosted by the root (`hosted_by`): its own
  transcript, durable log, credential and mesh peer id. Only the compute is shared. It is
  named `wf-{run slug}-{member}`. A name that would pass Forge's 63 character limit is
  shortened and ends in a short digest of the run and member, so it is still unique and still
  the same name each time.
- Every session of a run carries the tracker tag `workflow:{run slug}` and the metadata
  `workflow_run_id` and `workflow_member`.
- Ting creates the root held (not started), then each member hosted by it, then starts the
  root, which starts the unit with every member. A member created for a unit that has already
  started is refused with 409 ("a run's members are fixed when its unit starts"), and a member
  of a unit that does not exist with 404. Deleting a root that still has members is a 409; the
  members are deleted first.
- Forge is the record of which sessions a run has. Ting finds them again by name and tag
  (after a restart, or after a crash between creating a session and starting the unit) and
  never keeps a mapping that could disagree.
- Reassigning a step restarts the member's harness in place, under a new launch of the same
  session; a running unit cannot take a new member.
- When the run ends (completed, failed or canceled) Ting stops every session that carries the
  run's tag.

The unit's resources are the sum of its members' and the root's. A unit that does not fit
where it is scheduled fails to start and says so; nothing is shrunk. OpenShell admits at most
16 members per sandbox, and a larger run is refused at launch naming that bound
(see [limits](limits.md)).

The members of a run talk to each other, and the root, over the mesh (see
[agent messaging](agent-messaging.md)). Forge's `workflow_step_host` session contributor
gives every member `mesh.enabled: true` and the realm `workflow-{run id}`, so the run's members
find each other and no other run's; each announces and follows the topics its pinned persona
`produces` and `consumes`. Inside the unit the mesh is nng on loopback, discovered through a
file in a directory the unit's Skulds share. Configure the contributor with `join_mesh: false`
to run step sessions without the mesh.

Skuld cannot serve a run's steps without knowing which run it is in, so Ting sends each member's
create request the settings only the workflow knows, and Forge's contributor turns them into
`values.workflowSteps` (the `workflow_steps` section of Skuld's config; `SKULD__WORKFLOW_STEPS__...`
for the process, container, VM and sandbox runtimes). A member gets `runId`, `member`,
`personaAlias` (the alias the workflow gives its persona, which the offer's contracts are keyed
by), `eventFilters` (the stage member's, empty for none), `orchestration` (`engine` or `session`)
and `orchestratorPeerId`, the run root's mesh peer id, which is the root's session id. The root
gets `root: true`, `runId` and `orchestration`. Forge refuses a create that lacks any of them
(`workflow_persona_alias`, `workflow_event_filters`, `workflow_orchestration` and `hosted_by` on
a member; `workflow_orchestration` on the run's room) rather than starting a session Skuld
would refuse. A member whose stages give it different `eventFilters` is refused at launch,
because a run has one session per member.

The persona a session runs is the pinned portable document of the run's workflow snapshot,
sent to Forge whole; Forge checks it against its digest. The model is the stage member's
`model`, else the model the run was launched with, else `dispatch.default_model` (or the
adapter's `default_model`); with none of them the step fails with the remedy in the message.

## How a step is handed over

Ting does not call a session to give it a step. A step is an **offer**: the engine decides the
next activations, the ones one event produces for one node become one offer, and Ting posts the
signed offer (`service:ting`) to the root's `room/offer` operation
(`POST /api/v1/forge/sessions/{root}/room/offer`, owner-only). The root publishes it on the
run's mesh as a `workflow` event of kind `step.offered`, on the topic the node's members
consume.

- **Ting waits for the members.** Each member reports `listening` once its subscriptions are
  up. The offer is published once every member whose persona consumes its topic is listening;
  until then it waits, and after a restart the stuck sweep publishes what was waiting.
- **Who takes it.** A member whose pinned persona declares the topic in `consumes`. It checks
  the signature (a run accepts only the orchestrator's offers), its pin, the contract the offer
  carries (it derives the valid endings and verdicts from its own pin and refuses an offer that
  differs, naming both) and the node's filters for it. It then holds the step durably in its
  session's event log, reports `picked_up` (or `passed`, when the filters say it is not its),
  and starts the step at the next turn boundary. Each pickup is its own step in the ledger,
  fenced on the member's session.
- **Nobody takes it.** An offer published and not taken up or passed within
  `workflow_steps.pickup_grace_seconds` is flagged `unclaimed`, a person is told once, and the
  same event is published again, counted. The grace is when to look again, not a cap. An offer
  every member passed is an answer and is not published again. At the step deadline the step
  fails naming `workflow_steps.step_deadline_seconds`.
- **The run's sessions are stopped.** An offer goes out through the run's root, so while the
  root is stopped, failed or gone (a restart of the platform stops every session of the run)
  nothing can take it. The stuck sweep flags the offer `unit_stopped`, tells the owner once and
  raises an `attention` notification on the root session ("the run's sessions are stopped"):
  resuming the root starts every session of the run again, and Ting then hands the step out.
  The flag and the notification resolve once the root is up again; the step deadline still
  fails the step if the run is never resumed.

The offer carries what the session is told: the workflow, step, valid endings and verdicts,
outcome schema, deadline, and the rendered instructions: the inputs (the payloads of the
outcomes that led to the step, with their verdicts and notes) and the exact way to close the
step with the `workflow_step_close` tool.

The session closes the step through that tool. Skuld publishes `step.outcome` on the mesh and
reports the close to Forge as session activity (`metadata.workflow_step`, event `closed`),
and Ting applies it through the step engine, which fences it on the activation, the attempt
and the session that owns the step. The session id always comes from the authenticated activity
stream, never from the payload. A turn that ends with the step still open is reported as
`unclosed`; Ting writes it to the run's event log and counts it on
`ting_workflow_step_unclosed_total`, and never guesses an answer from the transcript.

Ting only records. Each Skuld reports its own acts to Forge on its activity stream
(`listening`, `offer_published`, `picked_up`, `passed`, `offer_refused`, `closed`,
`unclosed`), and a repeat of a report changes nothing. When the activity stream reconnects, and
at every stuck sweep, Ting reads the `workflow_step` frames from each run session's durable log
(`GET /sessions/{id}/log`) and records any it missed.

## Runs open at an upgrade

Step handoff used to be an HTTP call from Ting to the member's session, with a session per
member created when its first step activated. Those routes are gone. At start, Ting cancels every
run that was launched by that engine and is still open (a launched, unfinished run whose
workflow has stage members and that recorded no unit), with the reason
`canceled at upgrade: step handoff moved to the mesh`, and stops its sessions. Relaunch such a
run after the upgrade. A run that cannot be canceled is logged at error, counted on
`ting_workflow_run_upgrade_cancel_errors_total` and tried again at the next start.

## Gates and cancel

A gate is a step that waits for a person. A gate opens on its first delivery, so a gate
reached from alternative branches never waits for the ones not taken. A gate that declares
`joinMode: all` instead opens once every forward edge into it (from each producer of that
edge) holds an unused delivery of the current generation, like an `all` end; that is how
"Code, Review and Merge" holds its approval until the reviewer and the security auditor
have both passed. The Work board and the run's review page
decide it through Ting's own gate routes, which resolve it in the engine (recording who,
when and why); nothing is forwarded to a session.

No session holds a gate, so Forge never projects one into its notification feed. Ting
raises it there itself: when a gate
that waits for a person opens (not `automated_approval`, not `pendingBehavior: silent`),
Ting submits an `attention` / `warning` notification through
`POST /api/v1/forge/sessions/{id}/notifications` on the session of the step whose outcome
opened the gate (else the run's newest session), titled `{run}: {gate label}`, with the
gate's summary (or instructions) as the body and a link to `/ting/review/run/{slug}` on
`notification.public_origin`. Its idempotency and correlation key is
`workflow-gate:{run id}:{activation id}`. When the gate is decided, or the run ends with
it open, Ting resolves that notification (`PUT /notifications/{id}/resolve`) with what
happened. A call Forge refuses is logged at error, counted on
`ting_workflow_gate_notification_failures_total` and tried again on the run's next change
or sweep; the gate itself is unaffected. A gate opened before the run had any session
has nowhere to be recorded and is reported as degraded
(`ting.workflow_gate_notification`). The notification belongs to that session's owner. Canceling a run (A2A `CancelTask`, or
the owner canceling a durable execution) cancels its open steps and stops its sessions.

## Configuration

| Key | Default | Purpose |
|---|---|---|
| `workflow_steps.step_deadline_seconds` | 14400 | Deadline stamped on every offer and pickup; read by stuck detection, and the hard stop for an offer nobody takes up |
| `workflow_steps.pickup_grace_seconds` | 300 | How long a published offer may go without a pickup or a pass before it is flagged `unclaimed` and published again; when to look again, not a cap |
| `workflow_steps.catch_up_page_size` | 200 | Frames asked of a session's log per request when Ting reads back what the stream missed; bounds nothing |
| `workflow_steps.sessions.adapter` | `ting.adapters.forge_step_sessions.ForgeStepSessions` | The `StepSessionPort` class |
| `workflow_steps.sessions.kwargs` | `{}` | Plain kwargs for it (`name_prefix`, `default_model`) |

Rendered by `charts/ting` as `workflowSteps`.
