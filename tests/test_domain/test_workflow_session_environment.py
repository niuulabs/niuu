"""A workflow run's session started from the environment gets what the chart renders."""

from tests.support.workflow_steps import (
    MEMBER_MESH_ENV,
    MEMBER_MESH_VALUES,
    MEMBER_STEPS_ENV,
    MEMBER_STEPS_VALUES,
    ROOT_MESH_VALUES,
    ROOT_ROOM_ENV,
    ROOT_ROOM_VALUES,
    ROOT_STEPS_ENV,
    ROOT_STEPS_VALUES,
)
from volundr.domain.workflow_session import workflow_session_environment


def test_a_member_joins_the_runs_realm_and_shares_its_work() -> None:
    environment = workflow_session_environment(
        {"workflowSteps": MEMBER_STEPS_VALUES, "mesh": MEMBER_MESH_VALUES}
    )

    assert environment == {**MEMBER_STEPS_ENV, **MEMBER_MESH_ENV}
    assert not [name for name in environment if name.startswith("SKULD__ROOM__")]


def test_the_root_hosts_the_runs_room() -> None:
    environment = workflow_session_environment(
        {"workflowSteps": ROOT_STEPS_VALUES, "mesh": ROOT_MESH_VALUES, "room": ROOT_ROOM_VALUES}
    )

    assert environment == {**ROOT_STEPS_ENV, **ROOT_ROOM_ENV}


def test_a_room_without_its_own_environment_is_the_mesh_realms() -> None:
    room = {key: value for key, value in ROOT_ROOM_VALUES.items() if key != "environmentId"}

    environment = workflow_session_environment(
        {"workflowSteps": ROOT_STEPS_VALUES, "mesh": ROOT_MESH_VALUES, "room": room}
    )

    assert environment["SKULD__ROOM__ENVIRONMENT_ID"] == "workflow-run-1"


def test_an_ordinary_session_gets_no_run_settings() -> None:
    assert (
        workflow_session_environment({"mesh": MEMBER_MESH_VALUES, "room": ROOT_ROOM_VALUES}) == {}
    )
