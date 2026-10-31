"""A progress check on a finished run survives its artifacts being unreadable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.types import Task

from ting.api.a2a import WorkflowTaskHandler


def _handler(error: Exception | None) -> SimpleNamespace:
    return SimpleNamespace(_attach_artifacts=AsyncMock(side_effect=error))


async def test_a_mimir_that_refuses_ting_leaves_the_task_readable_and_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = httpx.Request("GET", "https://mimir.example/api/v1/mimir/pages")
    refused = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=request,
        response=httpx.Response(401, request=request),
    )
    task = Task(id="a2a-1")

    await WorkflowTaskHandler._attach_readable_artifacts(
        _handler(refused), task, SimpleNamespace(slug="a2a-1")
    )

    assert "401 Unauthorized" in task.metadata["artifacts_error"]
    assert "could not read the artifacts of task a2a-1" in caplog.text


async def test_readable_artifacts_add_no_error() -> None:
    task = Task(id="a2a-1")

    await WorkflowTaskHandler._attach_readable_artifacts(
        _handler(None), task, SimpleNamespace(slug="a2a-1")
    )

    assert "artifacts_error" not in task.metadata
