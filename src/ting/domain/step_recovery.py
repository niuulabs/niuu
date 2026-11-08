"""What an operator may do about a step, and when the engine refuses.

``check_recoverable`` is the one place that decides whether ``retry`` or ``reassign``
makes sense for a step as it is. It raises ``RecoveryRefusedError`` naming the reason,
and changes nothing.

A recovery starts a *new attempt* of the same node and member in the same generation.
It therefore needs a step that a session ran (a stage member's), that belongs to the
generation the run is in, and that is either failed, with no newer attempt, or open and
flagged stuck. A finished run is never revived: its terminal event is part of the
append-only log, so the run is started again instead.
"""

from __future__ import annotations

from uuid import UUID

from ting.domain.workflow_steps import (
    RecoveryAction,
    RecoveryRefusedError,
    RunState,
    StepKind,
    StepState,
    WorkflowStep,
)


def check_run_open(action: RecoveryAction, state: RunState, run_id: UUID) -> None:
    """Refuse unless the run was launched and has not finished."""
    if not state.launched:
        raise RecoveryRefusedError(action, f"run {run_id} was never launched")
    if state.finished:
        raise RecoveryRefusedError(
            action,
            f"run {run_id} already finished; a finished run is not revived, start a new run",
        )


def check_recoverable(
    action: RecoveryAction, state: RunState, run_id: UUID, activation_id: UUID
) -> WorkflowStep:
    """The step ``action`` applies to, or ``RecoveryRefusedError`` saying why not."""
    check_run_open(action, state, run_id)
    step = state.step(activation_id)
    if step is None or step.run_id != run_id:
        raise RecoveryRefusedError(
            action, f"run {run_id} has no step {activation_id}", missing=True
        )
    where = f"step {step.node_id}/{step.member} (attempt {step.attempt})"
    if step.kind is not StepKind.STAGE_MEMBER:
        raise RecoveryRefusedError(
            action,
            f"{where} is a {step.kind.value}, which no session runs; a gate is answered and "
            "a wait closes when what it waits on is observed",
        )
    if step.generation != state.generation:
        raise RecoveryRefusedError(
            action,
            f"{where} belongs to generation {step.generation} and the run is in generation "
            f"{state.generation}; rework replaced it",
        )
    refusal = _state_refusal(state, step)
    if refusal is not None:
        raise RecoveryRefusedError(action, f"{where} {refusal}")
    return step


def _state_refusal(state: RunState, step: WorkflowStep) -> str | None:
    if step.state is StepState.FAILED:
        newer = [
            other.attempt
            for other in state.steps
            if other.node_id == step.node_id
            and other.member == step.member
            and other.attempt > step.attempt
        ]
        if newer:
            return f"already has a newer attempt ({max(newer)}); recover that one"
        return None
    if step.state is StepState.PENDING:
        return "is queued behind a running step and has not started"
    if step.state in (StepState.ACTIVE, StepState.WAITING):
        if any(flag.activation_id == step.id for flag in state.live_flags()):
            return None
        return (
            f"is {step.state.value} and not flagged stuck; wait for it, or abort the run "
            "if it must stop"
        )
    return f"is {step.state.value}, so there is nothing to recover"


__all__ = ["check_recoverable", "check_run_open"]
