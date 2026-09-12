# Workflows, runs, and teams

Ting represents repeatable work as workflow definitions and tracks executions as
runs. Use it when the relationship between stages matters: for example, implement
a change, collect a review, and wait for approval before proceeding.

## Definition versus execution

A definition describes stages, transitions, resources, and gates. A run records
what happened for one input. Editing a definition and retrying a failed run are
different actions; inspect the run's recorded state before deciding what to change.

A session is one execution environment used to do work. A workflow can coordinate
sessions, but workflow state and session state are not interchangeable. A running
session may be waiting for input while its workflow is blocked at a gate.

## Gates and failure

A gate expresses a condition for progression. Put the decision and supporting
artifact together: a reviewer needs a diff or result, not merely a green stage.
When work fails, identify whether the failure is in dispatch, runtime startup,
provider access, the task itself, or a transition.

Retries may repeat side effects. Inspect the previous attempt and its outputs
before restarting work that creates external records, publishes changes, or
performs a deployment.

## Teams and collaboration

Several agents can specialize by persona and task. A workflow controls a process;
a collaboration room holds a conversation; mesh connects flock members directly.
Choose the mechanism for the job rather than treating every multi-agent exchange
as a workflow. See [inspect workflows and runs](../get-started/workflows-and-teams-step.md).
