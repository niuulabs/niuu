"""The refusal to run workflow steps: its message and its HTTP mapping (NIU-1198)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.workflow_runner_refusal import register_workflow_runner_refusal
from ting.config import AuthConfig, Settings
from ting.domain.exceptions import (
    WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE,
    WorkflowStepsHaveNoRunnerError,
)
from ting.main import create_app


def test_the_message_is_exact() -> None:
    assert (
        WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE
        == "workflow steps have no runner: steps will run on harness sessions (NIU-1198)"
    )
    assert str(WorkflowStepsHaveNoRunnerError()) == WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE


def _app_that_raises() -> FastAPI:
    app = FastAPI()
    register_workflow_runner_refusal(app)

    @app.post("/launch")
    async def launch() -> None:
        raise WorkflowStepsHaveNoRunnerError()

    return app


def test_the_refusal_is_a_501_carrying_the_message() -> None:
    response = TestClient(_app_that_raises()).post("/launch")

    assert response.status_code == 501
    assert response.json() == {"detail": WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE}


def test_without_the_handler_the_refusal_is_not_hidden_as_another_error() -> None:
    app = FastAPI()

    @app.post("/launch")
    async def launch() -> None:
        raise WorkflowStepsHaveNoRunnerError()

    with pytest.raises(WorkflowStepsHaveNoRunnerError):
        TestClient(app).post("/launch")


def test_the_ting_app_maps_the_refusal_to_501() -> None:
    app = create_app(settings=Settings(auth=AuthConfig(allow_anonymous_dev=True)))

    assert app.exception_handlers[WorkflowStepsHaveNoRunnerError].__name__ == (
        "workflow_steps_have_no_runner"
    )
