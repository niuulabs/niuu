"""Tests for WorkflowStepHostContributor."""

from volundr.adapters.outbound.contributors.workflow_step_host import (
    WorkflowStepHostContributor,
    workflow_realm,
)
from uuid import uuid4

from volundr.domain.models import GitSource, Session
from volundr.domain.projects import SessionCoordination, SessionReference
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


def _in_project(*, parent: str | None = None) -> Session:
    return Session(
        name="wf-run-coder",
        model="gpt",
        source=GitSource(),
        coordination=SessionCoordination(
            project_id=uuid4(),
            role="coder",
            parent=SessionReference(instance_id="valhalla", session_id=parent) if parent else None,
        ),
    )


async def test_the_runs_first_session_hosts_the_runs_room() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _in_project(), _step_host(workflow_run_id=RUN_ID, workflow_member="coder")
    )

    assert result.values["room"] == {
        "enabled": True,
        "environmentId": f"workflow-{RUN_ID}",
        "realmRoom": True,
    }


async def test_a_later_member_is_a_worker_of_the_room_not_another_host() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _in_project(parent=str(uuid4())),
        _step_host(workflow_run_id=RUN_ID, workflow_member="reviewer"),
    )

    assert "room" not in result.values
    assert result.values["mesh"] == {"enabled": True, "realmId": f"workflow-{RUN_ID}"}


async def test_a_run_in_no_project_or_without_the_mesh_hosts_no_room() -> None:
    no_project = await WorkflowStepHostContributor().contribute(
        _session(), _step_host(workflow_run_id=RUN_ID)
    )
    no_mesh = await WorkflowStepHostContributor(join_mesh=False).contribute(
        _in_project(), _step_host(workflow_run_id=RUN_ID)
    )
    declined = await WorkflowStepHostContributor(host_run_room=False).contribute(
        _in_project(), _step_host(workflow_run_id=RUN_ID)
    )

    assert "room" not in no_project.values
    assert "room" not in no_mesh.values
    assert "room" not in declined.values
