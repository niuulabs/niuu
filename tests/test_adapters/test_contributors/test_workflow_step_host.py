"""Tests for WorkflowStepHostContributor."""

from volundr.adapters.outbound.contributors.workflow_step_host import (
    WorkflowStepHostContributor,
)
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


def _session() -> Session:
    return Session(name="wf-run-coder", model="gpt", source=GitSource())


async def test_a_step_host_launch_enables_workflow_steps() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(), SessionContext(workload_config={"workflow_step_host": True})
    )

    assert result.values == {"workflowSteps": {"enabled": True}}


async def test_an_ordinary_launch_contributes_nothing() -> None:
    contributor = WorkflowStepHostContributor()

    assert (await contributor.contribute(_session(), SessionContext())).values == {}
    assert (
        await contributor.contribute(
            _session(), SessionContext(workload_config={"workflow_step_host": False})
        )
    ).values == {}
