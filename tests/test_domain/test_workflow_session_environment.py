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


TEAM_ROOT_VALUES = {
    "team": {"enabled": True},
    "mesh": {"enabled": True, "realmId": "team-1"},
    "room": {
        "enabled": True,
        "environmentId": "team-1",
        "realmRoom": True,
        "routed": True,
        "untargetedDelivery": "everyone",
    },
}


def test_a_teams_root_hosts_its_room_and_sends_untargeted_messages_to_everyone() -> None:
    environment = workflow_session_environment(TEAM_ROOT_VALUES)

    assert environment == {
        "SKULD__MESH__ENABLED": "true",
        "SKULD__MESH__REALM_ID": "team-1",
        "SKULD__ROOM__ENABLED": "true",
        "SKULD__ROOM__ENVIRONMENT_ID": "team-1",
        "SKULD__ROOM__REALM_ROOM": "true",
        "SKULD__ROOM__ROUTED": "true",
        "SKULD__ROOM__UNTARGETED_DELIVERY": "everyone",
    }
    assert not [name for name in environment if name.startswith("SKULD__WORKFLOW_STEPS__")]


def test_a_teams_member_joins_the_realm_and_shares_its_work_with_no_step_settings() -> None:
    environment = workflow_session_environment(
        {
            "team": {"enabled": True},
            "mesh": {"enabled": True, "realmId": "team-1", "shareWork": True},
        }
    )

    assert environment == {
        "SKULD__MESH__ENABLED": "true",
        "SKULD__MESH__REALM_ID": "team-1",
        "SKULD__MESH__SHARE_WORK": "true",
    }


def test_a_workflow_rooms_untargeted_delivery_is_left_to_skulds_own_default() -> None:
    environment = workflow_session_environment(
        {"workflowSteps": ROOT_STEPS_VALUES, "mesh": ROOT_MESH_VALUES, "room": ROOT_ROOM_VALUES}
    )

    assert "SKULD__ROOM__UNTARGETED_DELIVERY" not in environment


def test_a_session_with_room_values_but_no_team_or_run_gets_nothing() -> None:
    values = {key: value for key, value in TEAM_ROOT_VALUES.items() if key != "team"}

    assert workflow_session_environment(values) == {}
