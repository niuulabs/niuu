# Workflow steps on harness sessions

Ting runs a workflow as a **run**: a campaign plus a step ledger. Every stage member of
the workflow is a persona, and each persona runs on an ordinary harness session (Claude
Code, Codex and the others) that Forge creates. Ting never relays conversation between
agents; it activates steps and records how they end.

## One session per member, kept for the whole run

- A run has one session per stage member, created on the run's Forge connection when the
  member's first step activates and reused for every later step of that member, rework
  included.
- The session is named `wf-{run slug}-{member}`. A name that would pass Forge's 63
  character limit is shortened and ends in a short digest of the run and member, so it is
  still unique and still the same name each time.
- Every session of a run carries the tracker tag `workflow:{run slug}` and the metadata
  `workflow_run_id` and `workflow_member`.
- Forge is the record of which sessions a run has. Ting finds them again by name and tag
  (after a restart, or after a crash between creating a session and recording it) and
  never keeps a mapping that could disagree. A session found under the right name but
  the wrong tag stops the run with a message that names it; a failed session stops the
  run; a stopped session is started again.
- When the run ends (completed, failed or canceled) Ting stops every session that
  carries the run's tag.

The persona a session runs is the pinned portable document of the run's workflow
snapshot, sent to Forge whole; Forge checks it against its digest. The model is the stage
member's `model`, else the model the run was launched with, else `dispatch.default_model`
(or the adapter's `default_model`); with none of them the step fails with the remedy in
the message.

## A session that is still starting

A session takes minutes to boot, and Forge cannot reach its broker before it is running.
When a step is activated for a session that is not up yet (a member's first step, or a
stopped session being started again), Ting does not deliver it and does not fail it: the
step stays active and bound to the session, and Ting delivers it once Forge reports the
session running, polling every `workflow_steps.session_poll_seconds`. The caller (a launch,
or the close of the step before it) is not held for the boot. The wait ends when the
session is running, when it is stopped, failed or gone (the step fails and says so), or at
the step's deadline (the step fails naming `workflow_steps.step_deadline_seconds`).

The wait survives a restart: the ledger records when each step was delivered
(`workflow_steps.delivered_at`), and on start every Ting process waits again for each
active, bound, undelivered step. Setting `delivered_at` is a conditional update, so of
several workers or replicas waiting on the same step only one hands it over. A process
that dies between that claim and the send leaves the step claimed but undelivered; the
stuck sweep finds it (its session runs and does not know the activation) and delivers it.

## What a session is told, and how it answers

Ting delivers each activation to `POST /api/v1/forge/sessions/{id}/workflow/steps` with
the workflow, step, member, valid endings and verdicts, outcome schema, deadline, and the
rendered instructions: the inputs (the payloads of the outcomes that led to the step,
with their verdicts and notes) and the exact way to close the step with the
`workflow_step_close` tool.

The session closes the step through that tool. Skuld reports the close as session
activity (`metadata.workflow_step`, event `closed`), and Ting applies it through the step
engine, which fences it on the activation, the attempt and the session that owns the
step. The session id always comes from the authenticated activity stream, never from the
payload. A turn that ends with the step still open is reported as `unclosed`; Ting writes
it to the run's event log and counts it on `ting_workflow_step_unclosed_total`, and never
guesses an answer from the transcript.

When the activity stream reconnects, Ting asks each session that holds an open step for
the step's durable state and records any close it missed.

## Gates and cancel

A gate is a step that waits for a person. The Work board and the run's review page
decide it through Ting's own gate routes, which resolve it in the engine (recording who,
when and why); nothing is forwarded to a session.

No session holds a gate, so Forge cannot project it into its notification feed the way
it projects a session's `workflow_gate` frame. Ting raises it there itself: when a gate
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
| `workflow_steps.step_deadline_seconds` | 14400 | Deadline stamped on every stage-member activation; stored, read by stuck detection, and the bound on waiting for a starting session |
| `workflow_steps.session_poll_seconds` | 5 | How often Ting asks a starting session whether it is up; bounds nothing |
| `workflow_steps.sessions.adapter` | `ting.adapters.forge_step_sessions.ForgeStepSessions` | The `StepSessionPort` class |
| `workflow_steps.sessions.kwargs` | `{}` | Plain kwargs for it (`name_prefix`, `default_model`) |

Rendered by `charts/ting` as `workflowSteps`.
