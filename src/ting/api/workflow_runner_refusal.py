"""HTTP mapping of the refusal to run workflow steps.

Workflow steps have no runner until they run on harness sessions (NIU-1198).
Every route that would launch a workflow, or dispatch a step, lets
:class:`~ting.domain.exceptions.WorkflowStepsHaveNoRunnerError` propagate; the
app-level handler registered here turns it into a client-visible 501. The
request is valid and the server cannot do it, so it is neither a 4xx nor a 500,
and it is never reported as a failed item of a batch.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ting.domain.exceptions import WorkflowStepsHaveNoRunnerError


async def workflow_steps_have_no_runner(
    request: Request, exc: WorkflowStepsHaveNoRunnerError
) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_501_NOT_IMPLEMENTED, content={"detail": str(exc)})


def register_workflow_runner_refusal(app: FastAPI) -> None:
    """Map :class:`WorkflowStepsHaveNoRunnerError` to HTTP 501 on *app*."""
    app.add_exception_handler(WorkflowStepsHaveNoRunnerError, workflow_steps_have_no_runner)
