"""The StepSessionPort adapter against a fake Forge."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ting.adapters.forge_step_sessions import (
    FORGE_SESSION_NAME_MAX,
    ForgeStepSessions,
    StepSessionError,
    activation_body,
    step_session_name,
    step_session_tag,
)
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.workflow_steps import DeliveredEvent, PersonaRef, StepActivation

from . import workflow_step_contract as contract
from .fake_forge import FakeForge, FakeForgeFactory

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
        **kwargs,
    )
    return adapter, forge


def _activation(**overrides) -> StepActivation:
    values = {
        "run_id": UUID(contract.RUN_ID),
        "activation_id": UUID(contract.ACTIVATION_ID),
        "node_id": "implement",
        "node_label": "Implement",
        "member": "coder",
        "attempt": 1,
        "generation": 0,
        "session_id": "ses-1",
        "inputs": (
            DeliveredEvent(
                seq=2,
                event_type="work.requested",
                edge_id="e1",
                node_id="go",
                member=None,
                payload={"outcome": {"prompt": "add an endpoint"}},
            ),
        ),
        "deadline_at": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "valid_verdicts": ("implemented", "blocked"),
        "outcome_schema": {"summary": {"type": "string", "description": "what changed"}},
        "verdict_events": {"implemented": "code.ready", "blocked": "work.blocked"},
        "workflow_name": "Build",
    }
    values.update(overrides)
    return StepActivation(**values)


async def test_the_first_activation_creates_the_members_session_from_the_pinned_document() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    session_id = await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    [request] = forge.create_requests
    assert session_id == "ses-1"
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


async def test_later_activations_of_a_member_reuse_its_session() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    first = await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)
    second = await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    assert first == second
    assert len(forge.create_requests) == 1


async def test_a_ting_restart_finds_the_session_forge_already_has() -> None:
    campaign = _campaign()
    forge = FakeForge()
    existing = forge.add_session(
        "wf-add-endpoint-abc123-coder", step_session_tag(campaign.slug), status="running"
    )
    # A new adapter: nothing in memory from the earlier process.
    sessions, _ = _sessions(campaign, forge)

    assert await sessions.ensure_session(campaign.id, "coder", PERSONA_REF) == existing
    assert forge.create_requests == []


async def test_concurrent_activations_of_one_member_create_one_session() -> None:
    import asyncio

    campaign = _campaign()
    sessions, forge = _sessions(campaign)

    ids = await asyncio.gather(
        *(sessions.ensure_session(campaign.id, "coder", PERSONA_REF) for _ in range(3))
    )

    assert len(set(ids)) == 1
    assert len(forge.create_requests) == 1


async def test_a_stopped_session_is_started_again_not_replaced() -> None:
    campaign = _campaign()
    forge = FakeForge()
    existing = forge.add_session(
        "wf-add-endpoint-abc123-coder", step_session_tag(campaign.slug), status="stopped"
    )
    sessions, _ = _sessions(campaign, forge)

    assert await sessions.ensure_session(campaign.id, "coder", PERSONA_REF) == existing
    assert forge.started == [existing]


async def test_a_failed_session_stops_the_run_instead_of_being_replaced() -> None:
    campaign = _campaign()
    forge = FakeForge()
    forge.add_session(
        "wf-add-endpoint-abc123-coder", step_session_tag(campaign.slug), status="failed"
    )
    sessions, _ = _sessions(campaign, forge)

    with pytest.raises(StepSessionError, match="has failed"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    assert forge.create_requests == []


async def test_a_session_with_the_name_but_another_tag_is_refused() -> None:
    campaign = _campaign()
    forge = FakeForge()
    forge.add_session("wf-add-endpoint-abc123-coder", "workflow:someone-else")
    sessions, _ = _sessions(campaign, forge)

    with pytest.raises(StepSessionError, match="not run add-endpoint-abc123's session"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_forge_refusing_the_create_is_raised_not_swallowed() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    forge.fail_create = RuntimeError("422: persona digest mismatch")

    with pytest.raises(RuntimeError, match="persona digest mismatch"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_a_member_pinned_to_other_persona_than_its_document_is_refused() -> None:
    campaign = _campaign()
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="is persona 'coder', not the pinned 'other'"):
        await sessions.ensure_session(
            campaign.id, "coder", PersonaRef("coder", "other", "r1", "sha256:" + "0" * 64)
        )


async def test_a_member_with_no_pinned_document_is_refused() -> None:
    campaign = _campaign()
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="pins no persona document for member 'ghost'"):
        await sessions.ensure_session(campaign.id, "ghost", PERSONA_REF)


async def test_the_launch_model_is_used_when_the_stage_names_none() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"] = [{"personaId": "coder"}]
    campaign = _campaign(
        workflow_snapshot=snapshot,
        metadata={"model": "claude-opus-launch", "repo": "r", "branch": "b"},
    )
    sessions, forge = _sessions(campaign, default_model="configured-default")

    await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    assert forge.create_requests[0].model == "claude-opus-launch"


async def test_the_configured_default_model_is_the_last_resort_and_none_is_an_error() -> None:
    snapshot = _campaign().workflow_snapshot
    snapshot["graph"]["nodes"][0]["stageMembers"] = [{"personaId": "coder"}]
    campaign = _campaign(workflow_snapshot=snapshot)
    sessions, forge = _sessions(campaign, default_model="configured-default")

    await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)
    assert forge.create_requests[0].model == "configured-default"

    bare, _ = _sessions(campaign)
    with pytest.raises(StepSessionError, match="no model for member 'coder'"):
        await bare.ensure_session(campaign.id, "coder", PERSONA_REF)


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
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_a_connection_that_no_longer_resolves_is_an_error() -> None:
    campaign = _campaign(connection_id="gone")
    sessions, _ = _sessions(campaign)

    with pytest.raises(StepSessionError, match="no longer resolves"):
        await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)


async def test_a_run_without_a_connection_uses_its_owners_primary_forge() -> None:
    campaign = _campaign(connection_id=None)
    sessions, forge = _sessions(campaign)

    await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)

    assert len(forge.create_requests) == 1


async def test_a_run_with_no_campaign_is_an_error() -> None:
    sessions = ForgeStepSessions(
        volundr_factory=FakeForgeFactory(FakeForge()), campaigns=_Campaigns()
    )

    with pytest.raises(StepSessionError, match="has no campaign"):
        await sessions.ensure_session(uuid4(), "coder", PERSONA_REF)


async def test_delivery_posts_the_contract_body_with_rendered_instructions() -> None:
    campaign = _campaign(id=UUID(contract.RUN_ID))
    sessions, forge = _sessions(campaign)

    await sessions.deliver(_activation())

    [(session_id, body)] = forge.deliveries
    assert session_id == "ses-1"
    assert set(body) == contract.ACTIVATION_KEYS
    instructions = body.pop("instructions")
    expected = dict(contract.ACTIVATION_BODY)
    expected.pop("instructions")
    assert body == expected
    assert "workflow_step_close" in instructions
    assert "add an endpoint" in instructions


async def test_a_delivery_forge_refuses_is_raised() -> None:
    campaign = _campaign(id=UUID(contract.RUN_ID))
    sessions, forge = _sessions(campaign)
    forge.fail_deliver = RuntimeError("409: another step is open")

    with pytest.raises(RuntimeError, match="another step is open"):
        await sessions.deliver(_activation())


def test_an_activation_without_a_schema_or_deadline_sends_nulls() -> None:
    body = activation_body(_activation(outcome_schema=None, deadline_at=None))

    assert body["outcome_schema"] is None
    assert body["deadline_at"] is None


async def test_stopping_a_run_stops_every_session_it_owns_and_no_other() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)
    other = forge.add_session("someone-elses", "workflow:other-run")
    reviewer = forge.add_session("wf-add-endpoint-abc123-reviewer", step_session_tag(campaign.slug))

    await sessions.stop_run_sessions(campaign.id)

    assert sorted(forge.stopped) == ["ses-1", reviewer]
    assert other not in forge.stopped


async def test_a_session_that_cannot_be_stopped_is_reported_after_the_rest_were() -> None:
    campaign = _campaign()
    sessions, forge = _sessions(campaign)
    first = await sessions.ensure_session(campaign.id, "coder", PERSONA_REF)
    second = forge.add_session("wf-add-endpoint-abc123-reviewer", step_session_tag(campaign.slug))
    forge.fail_stop.add(first)

    with pytest.raises(StepSessionError, match="could not be stopped"):
        await sessions.stop_run_sessions(campaign.id)

    assert forge.stopped == [second]


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
