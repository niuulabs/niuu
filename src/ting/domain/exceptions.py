"""Domain exceptions for the Ting saga coordinator."""

from __future__ import annotations

from uuid import UUID


class WorkflowDocumentError(ValueError):
    """A portable workflow document is malformed or unsupported."""


class WorkflowConflictError(RuntimeError):
    """A workflow changed after the caller read it."""


class WorkflowReadOnlyError(RuntimeError):
    """A bundled read-only workflow cannot be changed in place."""


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted on a Run."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid state transition: {current} -> {target}")


class RunNotFoundError(Exception):
    """Raised when a run cannot be found by ID."""

    def __init__(self, run_id: UUID | str) -> None:
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")


WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE = (
    "workflow steps have no runner: steps will run on harness sessions (NIU-1198)"
)


class WorkflowStepsHaveNoRunnerError(RuntimeError):
    """A workflow was launched, or one of its steps dispatched, with no runner to execute it.

    Ting used to run workflow steps on the Ravn resident runtime (the
    ``ravn_flock`` session workload), which has been removed. Steps will run on
    harness sessions (Claude Code, Codex, OpenCode) driven by Skuld; until then
    every path that would start a workflow step raises this instead of starting
    a session that cannot do the work. The HTTP layer maps it to 501.
    """

    def __init__(self) -> None:
        super().__init__(WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE)
