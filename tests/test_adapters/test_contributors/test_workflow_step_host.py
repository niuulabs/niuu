"""Tests for WorkflowStepHostContributor."""

import pytest

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
        "mesh": {"enabled": True, "realmId": f"workflow-{RUN_ID}", "shareWork": True},
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


async def test_a_runs_room_hosts_the_runs_room_and_runs_no_agent() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(),
        SessionContext(workload_config={"workflow_run_room": True, "workflow_run_id": RUN_ID}),
    )

    assert result.values == {
        "mesh": {"enabled": True, "realmId": f"workflow-{RUN_ID}"},
        "room": {
            "enabled": True,
            "environmentId": f"workflow-{RUN_ID}",
            "realmRoom": True,
            "routed": True,
        },
    }


async def test_a_member_shares_its_work_with_the_runs_room_and_hosts_none() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(), _step_host(workflow_run_id=RUN_ID, workflow_member="reviewer")
    )

    assert "room" not in result.values
    assert result.values["mesh"]["shareWork"] is True


async def test_a_runs_room_on_a_forge_without_it_is_refused() -> None:
    context = SessionContext(workload_config={"workflow_run_room": True, "workflow_run_id": RUN_ID})

    with pytest.raises(ValueError, match="host_run_room"):
        await WorkflowStepHostContributor(host_run_room=False).contribute(_session(), context)
    with pytest.raises(ValueError, match="mesh"):
        await WorkflowStepHostContributor(join_mesh=False).contribute(_session(), context)


async def test_a_member_announces_its_persona_and_topics_from_its_pinned_persona() -> None:
    document = {
        "schema_version": 1,
        "id": "reviewer",
        "revision": "r1",
        "definition": {
            "name": "reviewer",
            "consumes": {"event_types": ["code.changed"]},
            "produces": {
                "event_type": "review.passed",
                "event_type_map": {"needs_changes": "review.changes_requested"},
            },
        },
    }

    result = await WorkflowStepHostContributor().contribute(
        _session(), _step_host(workflow_run_id=RUN_ID, persona_definition=document)
    )

    assert result.values["mesh"]["announce"] == {
        "persona": "reviewer",
        "consumes": ["code.changed"],
        # Every Skuld session also publishes code.changed after each turn.
        "emits": ["code.changed", "review.changes_requested", "review.passed"],
    }
