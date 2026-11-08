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
when and why); nothing is forwarded to a session. Canceling a run (A2A `CancelTask`, or
the owner canceling a durable execution) cancels its open steps and stops its sessions.

## Configuration

| Key | Default | Purpose |
|---|---|---|
| `workflow_steps.step_deadline_seconds` | 14400 | Deadline stamped on every stage-member activation; stored, and read by stuck detection |
| `workflow_steps.sessions.adapter` | `ting.adapters.forge_step_sessions.ForgeStepSessions` | The `StepSessionPort` class |
| `workflow_steps.sessions.kwargs` | `{}` | Plain kwargs for it (`name_prefix`, `default_model`) |

Rendered by `charts/ting` as `workflowSteps`.
