# Workflows, runs, and teams

Teams let agents combine different capabilities in pursuit of work. Workflows
connect those contributions into repeatable processes, including work commissioned
by another agent. A resident can request a tool build; specialists can research,
implement, and review it; the result returns to the resident for adoption.

The workflow service (Ting in the UI and configuration) represents repeatable
work as definitions and tracks executions as
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

## Workflows within the wider ecosystem

A workflow can be a capability another agent discovers and invokes. The agent
runtime uses A2A for interaction with external agents and workflows; configured service APIs
also allow people and other clients to launch and inspect work. This lets a
bounded process contribute to a larger responsibility without making the
workflow itself the owner of a resident's mandate.

Keep the requested outcome and returned artifact explicit at each boundary.
The commissioning agent must be able to judge whether the result meets its need.
A completed build, a reviewed artifact, and an adopted capability are different
outcomes. Shared knowledge and reusable tools carry useful results beyond the
run that produced them.

See [agents and residents](agents-and-personas.md) for judgment and adoption,
and [memory and knowledge](memory-and-knowledge.md) for carrying results forward.
