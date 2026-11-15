"""Tests for WorkflowStepHostContributor."""

from uuid import UUID

import pytest

from volundr.adapters.outbound.contributors.workflow_step_host import (
    WorkflowStepHostContributor,
    workflow_realm,
)
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext

RUN_ID = "9d8e8e8c-0c72-4283-a7c9-77c85263aa7b"
ROOT_ID = UUID("7a3f0b1e-2d5c-4e8a-9b6d-1c2e3f4a5b6c")

#: What a run's member launch carries besides the step-host flag.
MEMBER_FIELDS = {
    "workflow_run_id": RUN_ID,
    "workflow_member": "coder",
    "workflow_persona_alias": "coder-alias",
    "workflow_event_filters": {"language": "python"},
    "workflow_topics": ["code.requested", "merge.approved"],
    "workflow_orchestration": "engine",
}


def _session(hosted_by: UUID | None = ROOT_ID) -> Session:
    return Session(name="wf-run-coder", model="gpt", source=GitSource(), hosted_by=hosted_by)


def _step_host(**extra) -> SessionContext:
    return SessionContext(workload_config={"workflow_step_host": True, **MEMBER_FIELDS, **extra})


def _member_steps() -> dict:
    return {
        "enabled": True,
        "runId": RUN_ID,
        "member": "coder",
        "personaAlias": "coder-alias",
        "orchestratorPeerId": str(ROOT_ID),
        "eventFilters": {"language": "python"},
        "topics": ["code.requested", "merge.approved"],
        "orchestration": "engine",
    }


async def test_a_step_host_launch_enables_every_workflow_step_setting() -> None:
    result = await WorkflowStepHostContributor(join_mesh=False).contribute(_session(), _step_host())

    assert result.values == {"workflowSteps": _member_steps()}


async def test_a_member_launched_without_topics_subscribes_to_its_personas_own() -> None:
    fields = {key: value for key, value in MEMBER_FIELDS.items() if key != "workflow_topics"}

    result = await WorkflowStepHostContributor(join_mesh=False).contribute(
        _session(), SessionContext(workload_config={"workflow_step_host": True, **fields})
    )

    assert result.values["workflowSteps"]["topics"] == []


async def test_a_members_orchestrator_is_the_session_that_hosts_it() -> None:
    other_root = UUID("0d1c2b3a-4f5e-4d6c-8b7a-9a8b7c6d5e4f")

    result = await WorkflowStepHostContributor().contribute(
        _session(hosted_by=other_root), _step_host()
    )

    assert result.values["workflowSteps"]["orchestratorPeerId"] == str(other_root)


async def test_a_member_in_session_mode_and_without_filters_is_told_so() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(),
        _step_host(workflow_orchestration="session", workflow_event_filters={}),
    )

    steps = result.values["workflowSteps"]
    assert steps["orchestration"] == "session"
    assert steps["eventFilters"] == {}


async def test_a_step_host_of_a_run_joins_the_mesh_in_its_runs_realm() -> None:
    result = await WorkflowStepHostContributor().contribute(_session(), _step_host())

    assert result.values == {
        "workflowSteps": _member_steps(),
        "mesh": {"enabled": True, "realmId": f"workflow-{RUN_ID}", "shareWork": True},
    }


def test_a_runs_realm_fits_a_kubernetes_label_value() -> None:
    realm = workflow_realm(RUN_ID)

    assert len(realm) <= 63 and realm.replace("-", "").isalnum()


async def test_the_operator_can_run_step_sessions_without_the_mesh() -> None:
    result = await WorkflowStepHostContributor(join_mesh=False).contribute(_session(), _step_host())

    assert result.values == {"workflowSteps": _member_steps()}


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("workflow_run_id", "workflow_run_id"),
        ("workflow_member", "workflow_member"),
        ("workflow_persona_alias", "workflow_persona_alias"),
        ("workflow_event_filters", "workflow_event_filters"),
        ("workflow_orchestration", "workflow_orchestration"),
    ],
)
async def test_a_member_launched_without_a_setting_is_refused_naming_it(
    missing: str, named: str
) -> None:
    fields = {key: value for key, value in MEMBER_FIELDS.items() if key != missing}
    context = SessionContext(workload_config={"workflow_step_host": True, **fields})

    with pytest.raises(ValueError, match=named):
        await WorkflowStepHostContributor().contribute(_session(), context)


async def test_a_member_with_no_hosting_root_is_refused() -> None:
    with pytest.raises(ValueError, match="hosted_by"):
        await WorkflowStepHostContributor().contribute(_session(hosted_by=None), _step_host())


async def test_a_member_with_an_unknown_orchestration_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="workflow_orchestration"):
        await WorkflowStepHostContributor().contribute(
            _session(), _step_host(workflow_orchestration="mystery")
        )


async def test_every_missing_setting_is_named_at_once() -> None:
    context = SessionContext(workload_config={"workflow_step_host": True})

    with pytest.raises(ValueError) as refused:
        await WorkflowStepHostContributor().contribute(_session(hosted_by=None), context)

    for name in (
        "workflow_run_id",
        "workflow_member",
        "workflow_persona_alias",
        "workflow_event_filters",
        "workflow_orchestration",
        "hosted_by",
    ):
        assert name in str(refused.value)


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


def _room_context(**extra) -> SessionContext:
    return SessionContext(
        workload_config={
            "workflow_run_room": True,
            "workflow_run_id": RUN_ID,
            "workflow_orchestration": "engine",
            **extra,
        }
    )


async def test_a_runs_room_is_the_runs_root_and_runs_no_agent() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(hosted_by=None), _room_context()
    )

    assert result.values == {
        "workflowSteps": {"root": True, "runId": RUN_ID, "orchestration": "engine"},
        "mesh": {"enabled": True, "realmId": f"workflow-{RUN_ID}"},
        "room": {
            "enabled": True,
            "environmentId": f"workflow-{RUN_ID}",
            "realmRoom": True,
            "routed": True,
        },
    }


async def test_a_runs_root_in_session_mode_says_so() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(hosted_by=None), _room_context(workflow_orchestration="session")
    )

    assert result.values["workflowSteps"]["orchestration"] == "session"


async def test_a_root_launched_without_its_run_or_orchestration_is_refused() -> None:
    contributor = WorkflowStepHostContributor()

    with pytest.raises(ValueError, match="workflow_orchestration"):
        await contributor.contribute(
            _session(hosted_by=None),
            SessionContext(workload_config={"workflow_run_room": True, "workflow_run_id": RUN_ID}),
        )
    with pytest.raises(ValueError, match="workflow_run_id"):
        await contributor.contribute(
            _session(hosted_by=None),
            SessionContext(
                workload_config={
                    "workflow_run_room": True,
                    "workflow_orchestration": "engine",
                }
            ),
        )


async def test_a_member_shares_its_work_with_the_runs_room_and_hosts_none() -> None:
    result = await WorkflowStepHostContributor().contribute(
        _session(), _step_host(workflow_member="reviewer")
    )

    assert "room" not in result.values
    assert result.values["mesh"]["shareWork"] is True


async def test_a_runs_room_on_a_forge_without_it_is_refused() -> None:
    context = _room_context()

    with pytest.raises(ValueError, match="host_run_room"):
        await WorkflowStepHostContributor(host_run_room=False).contribute(
            _session(hosted_by=None), context
        )
    with pytest.raises(ValueError, match="mesh"):
        await WorkflowStepHostContributor(join_mesh=False).contribute(
            _session(hosted_by=None), context
        )


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
        _session(), _step_host(persona_definition=document)
    )

    assert result.values["mesh"]["announce"] == {
        "persona": "reviewer",
        "consumes": ["code.changed"],
        # Every Skuld session also publishes code.changed after each turn.
        "emits": ["code.changed", "review.changes_requested", "review.passed"],
    }
