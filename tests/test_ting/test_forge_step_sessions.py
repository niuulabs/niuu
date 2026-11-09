"""The StepSessionPort adapter against a fake Forge."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from ting.adapters.forge_step_sessions import (
    FORGE_SESSION_NAME_MAX,
    ForgeStepSessions,
    StepSessionError,
    run_room_name,
    step_session_name,
    step_session_tag,
)
from ting.domain.models import Saga, SagaStatus, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.run_thread import FORGE_OBJECTIVE_MAX, RunProjectError, run_objective
from ting.ports.volundr import ForgeProjectRef, RunRoomRequest, SessionMembership
from ting.ports.workflow_steps import PersonaRef, RunUnit, SessionLiveness

from .fake_forge import FakeForge, FakeForgeFactory, FakeSagas

PERSONA_DOCUMENT = {
    "schema_version": 1,
    "id": "coder",
    "revision": "r1",
    "definition": {"produces": {"event_type_map": {"implemented": "code.ready"}}},
}
PERSONA_REF = PersonaRef(alias="coder", id="coder", revision="r1", digest="sha256:" + "0" * 64)


def _campaign(**overrides) -> WorkflowCampaign:
    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        "slug": "add-endpoint-abc123",
        "name": "add-endpoint",
        "owner_id": "user-1",
        "workflow_id": uuid4(),
        "workflow_version": "1.0.0",
        "workflow_name": "Build",
        "workflow_snapshot": {
            "name": "Build",
            "graph": {
                "nodes": [
                    {
                        "id": "implement",
                        "kind": "stage",
                        "stageMembers": [{"personaId": "coder", "model": "claude-sonnet-4-6"}],
                    }
                ]
            },
            "persona_definitions": {"coder": PERSONA_DOCUMENT},
        },
        "session_id": "",
        "session_name": "",
        "status": WorkflowCampaignStatus.RUNNING,
        "active_stage_id": None,
        "stage_state": [],
        "metadata": {
            "repo": "https://github.com/niuulabs/volundr.git",
            "branch": "feat/x",
            "base_branch": "main",
        },
        "created_at": now,
        "updated_at": now,
        "connection_id": "cluster-1",
    }
    values.update(overrides)
    return WorkflowCampaign(**values)


class _Campaigns:
    def __init__(self, *campaigns: WorkflowCampaign) -> None:
        self._items = {campaign.id: campaign for campaign in campaigns}

    async def get_campaign(self, campaign_id: UUID):
        return self._items.get(campaign_id)


def _sessions(campaign: WorkflowCampaign, forge: FakeForge | None = None, **kwargs):
    forge = forge or FakeForge()
    adapter = ForgeStepSessions(
        volundr_factory=FakeForgeFactory(forge),
        campaigns=_Campaigns(campaign),
        **{"sagas": FakeSagas(), **kwargs},
    )
    return adapter, forge


async def _launched(campaign: WorkflowCampaign, forge: FakeForge | None = None, **kwargs):
    """A run whose unit is launched: ``(sessions, forge, unit)``."""
    sessions, forge = _sessions(campaign, forge, **kwargs)
    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})
    return sessions, forge, unit


# -- Launching a run: the root and every member, created together and started once ---------


async def test_launch_creates_root_then_hosted_members_then_starts_the_unit_once() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())

    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})

    assert isinstance(unit, RunUnit)
    [root_request] = forge.run_room_requests
    assert root_request.name == "wf-add-endpoint-abc123"
    assert root_request.hosts_members is True
    root = unit.root_id
    assert forge.unit_roots == {root: str(campaign.id)}
    coder, reviewer = forge.create_requests
    assert (coder.hosted_by, reviewer.hosted_by) == (root, root)
    assert unit.members == {
        "coder": forge.sessions[unit.members["coder"]].id,
        "reviewer": forge.sessions[unit.members["reviewer"]].id,
    }
    # The members were created while the unit was held, and only the root was started:
    # Forge starts the unit with every member at once.
    assert forge.lifecycle == [("start", root)]
    assert {forge.sessions[i].status for i in (root, *unit.members.values())} == {"starting"}
    assert [forge.launches[i] for i in unit.members.values()] == [1, 1]


async def test_a_member_is_created_from_its_pinned_document() -> None:
    campaign = _campaign()
    _, forge, unit = await _launched(campaign)

    [request] = forge.create_requests
    assert unit.members == {"coder": "ses-2"}
    assert request.persona_definition == PERSONA_DOCUMENT
    assert request.model == "claude-sonnet-4-6"
    assert request.name == "wf-add-endpoint-abc123-coder"
    assert request.tracker_issue_id == "workflow:add-endpoint-abc123"
    assert request.run_id == str(campaign.id)
    assert request.member == "coder"
    assert (request.repo, request.branch, request.base_branch) == (
        "https://github.com/niuulabs/volundr.git",
        "feat/x",
        "main",
    )


async def test_launching_a_launched_run_again_creates_and_starts_nothing() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    members = {"coder": PERSONA_REF, "reviewer": REVIEWER_REF}
    first = await sessions.launch_run(campaign.id, members)
    before = (len(forge.run_room_requests), len(forge.create_requests), list(forge.lifecycle))

    # A new adapter, as after a Ting restart: nothing in memory from the earlier process.
    again, _ = _sessions(campaign, forge)
    second = await again.launch_run(campaign.id, members)

    assert second == first
    assert (len(forge.run_room_requests), len(forge.create_requests), forge.lifecycle) == before


async def test_a_launch_that_stopped_half_way_creates_what_is_missing_and_starts_the_unit() -> None:
    campaign = _two_members()
    members = {"coder": PERSONA_REF, "reviewer": REVIEWER_REF}
    sessions, forge = _sessions(campaign, _in_project())
    original = forge.create_step_session
    calls = 0

    async def crash_on_the_second_member(request, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("Ting died here")
        return await original(request, **kwargs)

    forge.create_step_session = crash_on_the_second_member  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        await sessions.launch_run(campaign.id, members)
    assert forge.lifecycle == []  # the unit was not started with a member missing

    forge.create_step_session = original  # type: ignore[method-assign]
    resumed, _ = _sessions(campaign, forge)
    unit = await resumed.launch_run(campaign.id, members)

    assert len(forge.run_room_requests) == 1
    assert [r.member for r in forge.create_requests] == ["coder", "reviewer"]
    assert forge.lifecycle == [("start", unit.root_id)]


async def test_concurrent_launches_of_one_run_create_one_root_and_start_it_once() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    listed = forge.list_sessions

    async def list_slowly(*args, **kwargs):
        # Forge answers over the network: the list is taken, then the answer travels while
        # the other launch runs.
        answer = await listed(*args, **kwargs)
        await asyncio.sleep(0)
        return answer

    forge.list_sessions = list_slowly  # type: ignore[method-assign]

    units = await asyncio.gather(
        *(
            sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})
            for _ in range(3)
        )
    )

    assert len({unit.root_id for unit in units}) == 1
    assert len(forge.run_room_requests) == 1
    assert len(forge.create_requests) == 2
    assert forge.lifecycle == [("start", units[0].root_id)]


async def test_a_member_forge_refuses_to_host_surfaces_with_its_reason() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    create_root = forge.create_run_room

    async def create_root_started_elsewhere(request, **kwargs):
        root = await create_root(request, **kwargs)
        # Someone else starts the unit after it exists and before Ting attaches a member.
        forge._set(root.id, "starting")
        return root

    forge.create_run_room = create_root_started_elsewhere  # type: ignore[method-assign]

    with pytest.raises(StepSessionError) as refused:
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    message = str(refused.value)
    assert (
        "Forge refused to host member 'coder' in run add-endpoint-abc123's unit room-1" in message
    )
    assert "a run's members are fixed when its unit starts" in message
    assert isinstance(refused.value.__cause__, httpx.HTTPStatusError)
    assert forge.create_requests == []
    assert forge.lifecycle == []


async def test_a_member_missing_from_a_unit_that_already_started_is_refused() -> None:
    campaign = _two_members()
    sessions, forge, unit = await _launched(campaign, _in_project())

    with pytest.raises(StepSessionError, match="already started without member.*'reviewer'"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})

    assert [r.member for r in forge.create_requests] == ["coder"]
    assert forge.lifecycle == [("start", unit.root_id)]


async def test_a_launch_with_a_bad_member_creates_nothing() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    bad_ref = PersonaRef("reviewer", "other", "r1", "sha256:" + "0" * 64)

    with pytest.raises(StepSessionError, match="is persona 'reviewer', not the pinned 'other'"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": bad_ref})

    assert forge.run_room_requests == []
    assert forge.create_requests == []
    assert forge.lifecycle == []


async def test_a_launch_with_no_members_is_an_error() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    with pytest.raises(StepSessionError, match="has no stage members to launch"):
        await sessions.launch_run(campaign.id, {})

    assert forge.run_room_requests == []


async def test_a_unit_forge_cannot_start_is_an_error_naming_the_root() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    async def refuse(session_id, **kwargs):
        raise ConnectionError("no capacity for the unit")

    forge.start_session = refuse  # type: ignore[method-assign]

    with pytest.raises(StepSessionError, match=r"could not start run add-endpoint-abc123's unit"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_forge_refusing_a_create_for_another_reason_is_raised_as_it_is() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    forge.fail_create = RuntimeError("422: persona digest mismatch")

    with pytest.raises(RuntimeError, match="persona digest mismatch"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert forge.lifecycle == []


async def test_a_session_with_the_roots_name_but_another_tag_is_refused() -> None:
    campaign = _campaign()
    forge = FakeForge()
    forge.add_session("wf-add-endpoint-abc123", "workflow:someone-else")
    sessions, _ = _sessions(campaign, forge)

    with pytest.raises(StepSessionError, match="not run add-endpoint-abc123's root"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_a_member_name_held_by_another_runs_session_is_refused() -> None:
    campaign = _campaign()
    forge = FakeForge()
    forge.add_session("wf-add-endpoint-abc123-coder", "workflow:someone-else")
    sessions, _ = _sessions(campaign, forge)

    with pytest.raises(StepSessionError, match="not run add-endpoint-abc123's session"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert forge.run_room_requests == []


async def test_a_member_with_no_pinned_document_is_refused() -> None:
    campaign = _campaign()
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="pins no persona document for member 'ghost'"):
        await sessions.launch_run(campaign.id, {"ghost": PERSONA_REF})


async def test_the_launch_model_is_used_when_the_stage_names_none() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"] = [{"personaId": "coder"}]
    campaign = _campaign(
        workflow_snapshot=snapshot,
        metadata={"model": "claude-opus-launch", "repo": "r", "branch": "b"},
    )
    sessions, forge = _sessions(campaign, default_model="configured-default")

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert forge.create_requests[0].model == "claude-opus-launch"
    assert forge.run_room_requests[0].model == "claude-opus-launch"


async def test_the_configured_default_model_is_the_last_resort_and_none_is_an_error() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"] = [{"personaId": "coder"}]
    campaign = _campaign(workflow_snapshot=snapshot)
    sessions, forge = _sessions(campaign, default_model="configured-default")

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})
    assert forge.create_requests[0].model == "configured-default"

    bare, bare_forge = _sessions(campaign)
    with pytest.raises(StepSessionError, match="no model for member 'coder'"):
        await bare.launch_run(campaign.id, {"coder": PERSONA_REF})
    assert bare_forge.run_room_requests == []


async def test_the_members_extra_prompt_reaches_the_session_create() -> None:
    campaign = _campaign()
    member = campaign.workflow_snapshot["graph"]["nodes"][0]["stageMembers"][0]
    member["systemPromptExtra"] = "Only touch probe/."
    sessions, forge = _sessions(campaign)

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert forge.create_requests[0].system_prompt == "Only touch probe/."


async def test_a_member_without_an_extra_prompt_sends_none() -> None:
    campaign = _campaign()
    _, forge, _unit = await _launched(campaign)

    assert forge.create_requests[0].system_prompt == ""


async def test_a_member_given_two_extra_prompts_is_refused() -> None:
    campaign = _campaign()
    nodes = campaign.workflow_snapshot["graph"]["nodes"]
    nodes[0]["stageMembers"][0]["systemPromptExtra"] = "one"
    nodes.append(
        {
            "id": "fix",
            "kind": "stage",
            "stageMembers": [{"personaId": "coder", "system_prompt_extra": "two"}],
        }
    )
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="different systemPromptExtra"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_a_member_given_two_models_is_refused() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"].append(
        {
            "id": "polish",
            "kind": "stage",
            "stageMembers": [{"personaId": "coder", "model": "another-model"}],
        }
    )
    campaign = _campaign(workflow_snapshot=snapshot)
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="different models"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_a_member_and_the_root_are_told_the_alias_filters_and_orchestration() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"][0]["eventFilters"] = {"area": "backend"}
    snapshot["orchestration"] = {"mode": "session"}
    campaign = _campaign(workflow_snapshot=snapshot)

    _, forge, _ = await _launched(campaign)

    [member] = forge.create_requests
    assert member.persona_alias == PERSONA_REF.alias
    assert dict(member.event_filters) == {"area": "backend"}
    assert member.orchestration == "session"
    [root] = forge.run_room_requests
    assert root.orchestration == "session"


async def test_a_workflow_that_names_no_orchestration_runs_on_the_engine() -> None:
    _, forge, _ = await _launched(_campaign())

    [member] = forge.create_requests
    assert member.orchestration == "engine"
    assert dict(member.event_filters) == {}
    assert forge.run_room_requests[0].orchestration == "engine"


async def test_a_workflow_with_an_unknown_orchestration_creates_nothing() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["orchestration"] = {"mode": "mystery"}
    campaign = _campaign(workflow_snapshot=snapshot)
    sessions, forge = _sessions(campaign)

    with pytest.raises(StepSessionError, match="orchestration"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert forge.run_room_requests == []
    assert forge.create_requests == []


async def test_a_member_given_two_sets_of_event_filters_is_refused() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"][0]["eventFilters"] = {"area": "backend"}
    snapshot["graph"]["nodes"].append(
        {
            "id": "polish",
            "kind": "stage",
            "stageMembers": [{"personaId": "coder", "eventFilters": {"area": "frontend"}}],
        }
    )
    campaign = _campaign(workflow_snapshot=snapshot)
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="different eventFilters"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_a_connection_that_no_longer_resolves_is_an_error() -> None:
    campaign = _campaign(connection_id="gone")
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="no longer resolves"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})


async def test_a_run_without_a_connection_uses_its_owners_primary_forge() -> None:
    campaign = _campaign(connection_id=None)
    sessions, forge = _sessions(campaign)

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    assert len(forge.create_requests) == 1


async def test_a_run_with_no_campaign_is_an_error() -> None:
    sessions = ForgeStepSessions(
        volundr_factory=FakeForgeFactory(FakeForge()), campaigns=_Campaigns(), sagas=FakeSagas()
    )

    with pytest.raises(StepSessionError, match="has no campaign"):
        await sessions.launch_run(uuid4(), {"coder": PERSONA_REF})


# -- A member's session after launch -------------------------------------------------------


async def test_a_launched_members_session_is_found_again() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)

    assert await sessions.ensure_session(campaign.id, "coder", PERSONA_REF) == unit.members["coder"]
    assert len(forge.create_requests) == 1


async def test_a_run_that_was_not_launched_has_no_member_sessions_to_find() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    with pytest.raises(StepSessionError, match="has no unit on Forge: launch the run"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    assert forge.create_requests == []


async def test_a_member_the_unit_was_not_launched_with_is_not_created_later() -> None:
    campaign = _two_members()
    sessions, forge, _unit = await _launched(campaign, _in_project())

    with pytest.raises(StepSessionError, match="members are fixed when its unit starts"):
        await sessions.ensure_session(campaign.id, "reviewer", REVIEWER_REF)

    assert [r.member for r in forge.create_requests] == ["coder"]


async def test_a_stopped_member_is_started_again_not_replaced() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    member = unit.members["coder"]
    forge.set_status(member, "stopped")

    assert await sessions.ensure_session(campaign.id, "coder", PERSONA_REF) == member
    assert forge.started[-1] == member
    assert forge.launches[member] == 2


async def test_a_failed_member_stops_the_run_instead_of_being_restarted() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    forge.set_status(unit.members["coder"], "failed")

    with pytest.raises(StepSessionError, match="has failed"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_a_member_session_with_another_tag_is_refused() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    forge.sessions[unit.members["coder"]] = replace(
        forge.sessions[unit.members["coder"]], tracker_issue_id="workflow:someone-else"
    )

    with pytest.raises(StepSessionError, match="not run add-endpoint-abc123's session"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_stopping_a_run_stops_its_members_first_then_its_root_and_no_other_session() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})
    other = forge.add_session("someone-elses", "workflow:other-run")
    forge.lifecycle.clear()

    await sessions.stop_run_sessions(campaign.id)

    assert forge.lifecycle == [
        *[("stop", member) for member in unit.members.values()],
        ("stop", unit.root_id),
    ]
    assert other not in forge.stopped


async def test_stopping_a_run_skips_sessions_that_never_started_or_already_stopped() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    # A launch that died before it started the unit: the root and a member are only held.
    root = await forge.create_run_room(
        RunRoomRequest(
            name="wf-add-endpoint-abc123",
            model="m",
            tracker_issue_id=step_session_tag(campaign.slug),
            run_id=str(campaign.id),
            orchestration="engine",
            hosts_members=True,
        )
    )
    forge.add_session(
        "wf-add-endpoint-abc123-coder", step_session_tag(campaign.slug), status="stopped"
    )
    assert forge.sessions[root.id].status == "created"

    await sessions.stop_run_sessions(campaign.id)

    assert forge.stopped == []


async def test_a_session_that_cannot_be_stopped_is_reported_after_the_rest_were() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})
    coder, reviewer = unit.members["coder"], unit.members["reviewer"]
    forge.fail_stop.add(coder)

    with pytest.raises(StepSessionError, match="could not be stopped"):
        await sessions.stop_run_sessions(campaign.id)

    assert forge.stopped == [reviewer, unit.root_id]


def test_session_names_are_forge_labels_within_the_limit() -> None:
    run_id = uuid4()
    short = step_session_name("add-endpoint-abc123", "coder", run_id)
    long = step_session_name("a" * 90 + "-0123456789ab", "Reviewer_Security", run_id)
    other_member = step_session_name("a" * 90 + "-0123456789ab", "Reviewer_Docs", run_id)

    assert short == "wf-add-endpoint-abc123-coder"
    assert len(long) <= FORGE_SESSION_NAME_MAX
    assert long != other_member
    assert long == step_session_name("a" * 90 + "-0123456789ab", "Reviewer_Security", run_id)
    for name in (short, long, other_member):
        assert name == name.lower()
        assert not name.startswith("-") and not name.endswith("-")
        assert all(ch.isalnum() or ch == "-" for ch in name)


# --- observing a step's session (stuck detection) ------------------------------------


@pytest.mark.parametrize(
    ("status", "liveness"),
    [
        ("created", SessionLiveness.STARTING),
        ("starting", SessionLiveness.STARTING),
        ("provisioning", SessionLiveness.STARTING),
        ("running", SessionLiveness.RUNNING),
        ("stopped", SessionLiveness.STOPPED),
        ("stopping", SessionLiveness.STOPPED),
        ("archived", SessionLiveness.STOPPED),
        ("failed", SessionLiveness.FAILED),
    ],
)
async def test_liveness_is_the_status_forge_reports_and_asks_nothing_of_the_session(
    status: str, liveness: SessionLiveness
) -> None:
    campaign = _campaign()
    forge = FakeForge()
    session_id = forge.add_session("wf-add-endpoint-abc123-coder", "workflow:x", status=status)
    sessions, _ = _sessions(campaign, forge)

    assert await sessions.session_liveness(campaign.id, session_id) is liveness
    assert forge.room_offers == [] and forge.log_reads == []


async def test_liveness_of_a_session_forge_no_longer_has_is_gone() -> None:
    campaign = _campaign()
    sessions, _ = _sessions(campaign)

    assert await sessions.session_liveness(campaign.id, "ses-404") is SessionLiveness.GONE


# --- restarting a member in place ---------------------------------------------------


async def test_replacing_a_running_member_restarts_it_in_place_under_a_new_launch() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})
    coder, reviewer = unit.members["coder"], unit.members["reviewer"]
    forge.set_status(coder, "running")
    forge.set_status(reviewer, "running")
    forge.lifecycle.clear()
    creates = len(forge.create_requests)

    again = await sessions.replace_session(campaign.id, "coder", PERSONA_REF, coder)

    # The same session: nothing was created, only the member stopped and started again.
    assert again == coder
    assert len(forge.create_requests) == creates
    assert forge.lifecycle == [("stop", coder), ("start", coder)]
    assert forge.launches[coder] == 2
    assert forge.launches[reviewer] == 1
    assert forge.sessions[coder].status == "starting"
    assert forge.sessions[reviewer].status == "running"
    assert forge.sessions[unit.root_id].status == "starting"
    # And it is still the member's session afterwards.
    assert await sessions.ensure_session(campaign.id, "coder", PERSONA_REF) == coder


async def test_a_member_can_be_restarted_in_place_again_and_again() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    member = unit.members["coder"]

    for _ in range(3):
        forge.set_status(member, "running")
        assert await sessions.replace_session(campaign.id, "coder", PERSONA_REF, member) == member

    assert forge.launches[member] == 4
    assert len(forge.create_requests) == 1


async def test_restarting_a_member_that_is_already_down_only_starts_it() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    member = unit.members["coder"]
    forge.set_status(member, "failed")
    forge.lifecycle.clear()

    assert await sessions.replace_session(campaign.id, "coder", PERSONA_REF, member) == member

    assert forge.lifecycle == [("start", member)]


async def test_restarting_with_no_session_named_restarts_the_members_session() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    forge.set_status(unit.members["coder"], "running")

    assert (
        await sessions.replace_session(campaign.id, "coder", PERSONA_REF, None)
        == (unit.members["coder"])
    )


async def test_a_session_that_is_not_the_members_is_not_restarted() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    forge.set_status(unit.members["coder"], "running")
    forge.lifecycle.clear()

    with pytest.raises(StepSessionError, match="the member's session in the unit is"):
        await sessions.replace_session(campaign.id, "coder", PERSONA_REF, "ses-elsewhere")

    assert forge.lifecycle == []


async def test_a_member_that_cannot_be_stopped_is_not_restarted() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    member = unit.members["coder"]
    forge.set_status(member, "running")
    forge.fail_stop.add(member)
    forge.lifecycle.clear()

    with pytest.raises(StepSessionError, match="could not be stopped"):
        await sessions.replace_session(campaign.id, "coder", PERSONA_REF, member)

    assert forge.lifecycle == []
    assert forge.launches[member] == 1


async def test_a_restart_forge_refuses_is_an_error_naming_the_member() -> None:
    campaign = _campaign()
    sessions, forge, unit = await _launched(campaign)
    member = unit.members["coder"]
    forge.set_status(member, "running")
    forge.set_status(unit.root_id, "stopped")  # the unit is down: a member cannot start alone

    with pytest.raises(StepSessionError, match="could not be restarted in place"):
        await sessions.replace_session(campaign.id, "coder", PERSONA_REF, member)


async def test_a_run_with_no_unit_or_no_such_member_has_nothing_to_restart() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    with pytest.raises(StepSessionError, match="has no unit on Forge"):
        await sessions.replace_session(campaign.id, "coder", PERSONA_REF, None)

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})
    with pytest.raises(StepSessionError, match="no session for member 'reviewer'"):
        await sessions.replace_session(campaign.id, "reviewer", REVIEWER_REF, None)
    assert len(forge.create_requests) == 1


# --- handing an offer to the root ----------------------------------------------------

OFFER = {
    "kind": "step.offered",
    "offer_id": "offer-1",
    "run_id": "run-1",
    "node_id": "implement",
    "topic": "code.requested",
}


async def test_an_offer_is_handed_to_the_runs_root_verbatim() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())
    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})

    await sessions.publish_offer(campaign.id, OFFER)
    await sessions.publish_offer(campaign.id, OFFER)

    # One call per offer, to the root and not to a member; re-handing the same offer is the
    # same event, so the repeat is safe.
    assert forge.room_offers == [(unit.root_id, OFFER), (unit.root_id, OFFER)]
    assert unit.root_id not in unit.members.values()


async def test_an_offer_for_a_run_that_was_not_launched_is_refused() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    with pytest.raises(StepSessionError, match="no root session to publish an offer on"):
        await sessions.publish_offer(campaign.id, OFFER)

    assert forge.room_offers == []


async def test_forge_refusing_an_offer_is_raised_not_swallowed() -> None:
    campaign = _campaign()
    sessions, forge, _unit = await _launched(campaign)
    forge.fail_offer = RuntimeError("502: the root's broker is not up")

    with pytest.raises(RuntimeError, match="the root's broker is not up"):
        await sessions.publish_offer(campaign.id, OFFER)

    assert forge.room_offers == []


# -- A run as one thread: its sessions join the run's project -------------------------


def _saga(**overrides) -> Saga:
    values = {
        "id": uuid4(),
        "tracker_id": "proj-1",
        "tracker_type": "linear",
        "slug": "billing",
        "name": "Billing",
        "repos": [],
        "feature_branch": "",
        "status": SagaStatus.ACTIVE,
        "created_at": datetime.now(UTC),
        "base_branch": "main",
        "owner_id": "user-1",
    }
    values.update(overrides)
    return Saga(**values)


def _with(campaign: WorkflowCampaign, **metadata) -> WorkflowCampaign:
    return replace(
        campaign,
        metadata={**campaign.metadata, "prompt": "Add the export endpoint\n\nDetails", **metadata},
    )


PROJECT_ID = "11111111-1111-1111-1111-111111111111"


def _in_project(forge: FakeForge | None = None) -> FakeForge:
    forge = forge or FakeForge()
    forge.projects[PROJECT_ID] = ForgeProjectRef(id=PROJECT_ID, name="Probe", instance_id="forge-a")
    return forge


async def test_a_run_launched_into_a_project_puts_the_root_and_each_member_in_it() -> None:
    campaign = _with(_campaign(), forge_project_id=PROJECT_ID)
    sessions, forge = _sessions(campaign, _in_project())

    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    # The root comes first: the run's room, the coordinator of the run, held as the unit root.
    [room] = forge.run_room_requests
    assert room.name == "wf-add-endpoint-abc123"
    assert room.tracker_issue_id == "workflow:add-endpoint-abc123"
    assert room.membership == SessionMembership(
        project_id=PROJECT_ID,
        role="coordinator",
        objective="Build: Add the export endpoint",
        labels=("workflow-run:add-endpoint-abc123",),
    )
    [request] = forge.create_requests
    assert request.hosted_by == unit.root_id == "room-1"
    assert request.membership == SessionMembership(
        project_id=PROJECT_ID,
        role="coder",
        objective="Build: Add the export endpoint",
        labels=("workflow-run:add-endpoint-abc123",),
        parent_instance_id="forge-a",
        parent_session_id="room-1",
    )
    # A project the run was launched into already exists; Ting creates none.
    assert forge.projects_created == []


async def test_a_tickets_run_joins_the_tickets_project_creating_it_under_its_id() -> None:
    saga = _saga()
    campaign = _with(_campaign(), ticket={"sagaId": str(saga.id), "issueId": "issue-1"})
    sessions, forge = _sessions(campaign, sagas=FakeSagas(saga))

    await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    [request] = forge.create_requests
    assert request.membership is not None
    assert request.membership.project_id == str(saga.forge_project_id)
    assert forge.run_room_requests[0].membership.project_id == str(saga.forge_project_id)
    assert forge.projects[str(saga.forge_project_id)].name == "Billing"


async def test_a_run_in_no_project_still_has_its_root_and_hosted_members() -> None:
    campaign = _with(_campaign())
    sessions, forge = _sessions(campaign)

    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})

    [room] = forge.run_room_requests
    assert room.membership is None and room.hosts_members is True
    [request] = forge.create_requests
    assert request.membership is None
    assert request.hosted_by == unit.root_id
    assert forge.lifecycle == [("start", unit.root_id)]


async def test_a_ticket_whose_project_is_gone_stops_the_launch_before_anything_is_created() -> None:
    campaign = _with(_campaign(), ticket={"sagaId": str(uuid4()), "issueId": "issue-1"})
    sessions, forge = _sessions(campaign)

    with pytest.raises(RunProjectError, match="no longer has"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})
    assert forge.run_room_requests == []
    assert forge.create_requests == []


async def test_a_project_its_forge_lacks_stops_the_launch_before_anything_is_created() -> None:
    campaign = _with(_campaign(), forge_project_id=PROJECT_ID)
    sessions, forge = _sessions(campaign)

    with pytest.raises(StepSessionError, match="does not have"):
        await sessions.launch_run(campaign.id, {"coder": PERSONA_REF})
    assert forge.run_room_requests == []
    assert forge.create_requests == []


def _two_members() -> WorkflowCampaign:
    reviewer = {**PERSONA_DOCUMENT, "id": "reviewer"}
    campaign = _with(_campaign(), forge_project_id=PROJECT_ID)
    snapshot = campaign.workflow_snapshot
    nodes = [
        *snapshot["graph"]["nodes"],
        {
            "id": "review",
            "kind": "stage",
            "stageMembers": [{"personaId": "reviewer", "model": "gpt-5.6-terra"}],
        },
    ]
    return replace(
        campaign,
        workflow_snapshot={
            **snapshot,
            "graph": {"nodes": nodes},
            "persona_definitions": {**snapshot["persona_definitions"], "reviewer": reviewer},
        },
    )


REVIEWER_REF = PersonaRef(
    alias="reviewer", id="reviewer", revision="r1", digest="sha256:" + "0" * 64
)


async def test_every_member_nests_under_the_runs_one_root() -> None:
    campaign = _two_members()
    sessions, forge = _sessions(campaign, _in_project())

    unit = await sessions.launch_run(campaign.id, {"coder": PERSONA_REF, "reviewer": REVIEWER_REF})

    assert len(forge.run_room_requests) == 1
    coder, reviewer = forge.create_requests
    assert coder.membership.parent_session_id == unit.root_id
    assert (reviewer.membership.parent_instance_id, reviewer.membership.parent_session_id) == (
        "forge-a",
        unit.root_id,
    )
    assert reviewer.membership.role == "reviewer"
    assert (reviewer.model, reviewer.hosted_by) == ("gpt-5.6-terra", unit.root_id)


def test_an_objective_past_forges_limit_is_cut_and_counted(monkeypatch) -> None:
    hits: list[dict] = []
    monkeypatch.setattr(
        "ting.domain.services.run_thread.record_truncation",
        lambda knob, **kwargs: hits.append({"knob": knob, **kwargs}),
    )
    campaign = _with(_campaign(), prompt="x" * (FORGE_OBJECTIVE_MAX + 50))

    objective = run_objective(campaign)

    assert len(objective) == FORGE_OBJECTIVE_MAX
    assert objective.endswith("…")
    [hit] = hits
    assert hit["kept"] == FORGE_OBJECTIVE_MAX


def test_a_room_name_fits_forge_limit_however_long_the_slug() -> None:
    run_id = uuid4()
    name = run_room_name("x" * 120, run_id)

    assert len(name) <= FORGE_SESSION_NAME_MAX
    assert name.startswith("wf-x")
    assert run_room_name("short-run", run_id) == "wf-short-run"
