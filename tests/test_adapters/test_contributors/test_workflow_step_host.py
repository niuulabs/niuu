"""Tests for WorkflowStepHostContributor."""

from volundr.adapters.outbound.contributors.workflow_step_host import (
    WorkflowStepHostContributor,
    workflow_realm,
)
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext

RUN_ID = "9d8e8e8c-0c72-4283-a7c9-77c85263aa7b"


def _session() -> Session:
    return Session(name="wf-run-coder", model="gpt", source=GitSource())


def _step_host(**extra) -> SessionContext:
    return SessionContext(workload_config={"workflow_step_host": True, **extra})


async def test_a_step_host_launch_enables_workflow_steps() -> None:
    result = await WorkflowStepHostContributor().contribute(_session(), _step_host())

    assert result.values == {"workflowSteps": {"enabled": True}}


async def test_a_step_host_of_a_run_joins_the_mesh_in_its_runs_realm() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(), _step_host(workflow_run_id=RUN_ID, workflow_member="coder")
    )

    assert result.values == {
        "workflowSteps": {"enabled": True},
        "mesh": {"enabled": True, "realmId": f"workflow-{RUN_ID}"},
    }


def test_a_runs_realm_fits_a_kubernetes_label_value() -> None:
    realm = workflow_realm(RUN_ID)

    assert len(realm) <= 63 and realm.replace("-", "").isalnum()


async def test_the_operator_can_run_step_sessions_without_the_mesh() -> None:
    result = await WorkflowStepHostContributor(join_mesh=False).contribute(
        _session(), _step_host(workflow_run_id=RUN_ID)
    )

    assert result.values == {"workflowSteps": {"enabled": True}}


async def test_an_ordinary_launch_contributes_nothing() -> None:
    contributor = WorkflowStepHostContributor()

    assert (await contributor.contribute(_session(), SessionContext())).values == {}
    assert (
        await contributor.contribute(
            _session(),
            SessionContext(
                workload_config={"workflow_step_host": False, "workflow_run_id": RUN_ID}
            ),
        )
    ).values == {}
