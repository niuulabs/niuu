"""Stored attribution, read filtering, and mutation checks using real Cedar."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import Principal
from identity.ports import AuthorizationDeniedError
from ting.domain.models import RunStatus, WorkflowScope
from ting.domain.services.resource_authorization import (
    AuthorizedCampaignRepository,
    AuthorizedSagaRepository,
    AuthorizedWorkflowRepository,
)


@pytest.fixture
def principal():
    return Principal("alice", "", "acme", ["volundr:developer"])


@pytest.mark.parametrize(
    "wrapper,kind",
    [
        (AuthorizedSagaRepository, "saga"),
        (AuthorizedCampaignRepository, "campaign"),
        (AuthorizedWorkflowRepository, "workflow"),
    ],
)
async def test_stored_tenant_controls_reads_and_mutations(wrapper, kind, principal):
    own = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme", scope=WorkflowScope.USER)
    foreign = SimpleNamespace(
        id=uuid4(), owner_id="alice", tenant_id="other", scope=WorkflowScope.USER
    )
    legacy = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="", scope=WorkflowScope.USER)
    repo = AsyncMock()
    getter = getattr(repo, f"get_{kind}")
    getter.return_value = foreign
    policy = wrapper(repo, CedarAuthorizationAdapter(), principal)
    assert await getattr(policy, f"get_{kind}")(foreign.id) is None
    with pytest.raises(AuthorizationDeniedError):
        await getattr(policy, f"save_{kind}")(foreign)
    getattr(repo, f"save_{kind}").assert_not_called()
    getter.return_value = legacy
    assert await getattr(policy, f"get_{kind}")(legacy.id) is None
    getter.return_value = own
    assert await getattr(policy, f"get_{kind}")(own.id) is own
    await getattr(policy, f"save_{kind}")(own)


async def test_seeded_global_workflow_is_readable_but_never_tenant_editable(principal):
    workflow = SimpleNamespace(id=uuid4(), owner_id=None, tenant_id="", scope=WorkflowScope.SYSTEM)
    repo = AsyncMock()
    repo.get_workflow.return_value = workflow
    policy = AuthorizedWorkflowRepository(
        repo, CedarAuthorizationAdapter(), replace(principal, roles=["volundr:admin"])
    )
    assert await policy.get_workflow(workflow.id) is workflow
    with pytest.raises(AuthorizationDeniedError):
        await policy.save_workflow(workflow)
    with pytest.raises(AuthorizationDeniedError):
        await policy.delete_workflow(workflow.id)


async def test_viewer_cannot_load_saga_for_external_mutation(principal):
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    repo = AsyncMock()
    repo.get_saga.return_value = saga
    policy = AuthorizedSagaRepository(
        repo,
        CedarAuthorizationAdapter(),
        replace(principal, roles=["volundr:viewer"]),
        read_action="update",
    )
    assert await policy.get_saga(saga.id) is None
    policy.read_action = "read"
    assert await policy.get_saga(saga.id) is saga


async def test_no_auth_retains_access_to_legacy_resources(principal):
    saga = SimpleNamespace(id=uuid4(), owner_id=None, tenant_id="")
    repo = AsyncMock()
    repo.get_saga.return_value = saga
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)
    assert await policy.get_saga(saga.id) is saga
    await policy.save_saga(saga)


async def test_cannot_reparent_phase_or_run(principal):
    repo = AsyncMock()
    repo.get_saga.return_value = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    repo.get_phase.return_value = SimpleNamespace(id=uuid4(), saga_id=uuid4())
    policy = AuthorizedSagaRepository(repo, CedarAuthorizationAdapter(), principal)
    with pytest.raises(AuthorizationDeniedError):
        await policy.save_phase(SimpleNamespace(id=uuid4(), saga_id=uuid4()))
    repo.get_run.return_value = SimpleNamespace(id=uuid4(), phase_id=uuid4())
    with pytest.raises(AuthorizationDeniedError):
        await policy.save_run(SimpleNamespace(id=uuid4(), phase_id=uuid4()))
    repo.save_phase.assert_not_called()
    repo.save_run.assert_not_called()


@pytest.mark.parametrize(
    "roles,tenant", [(["volundr:viewer"], "acme"), (["volundr:admin"], "other")]
)
async def test_launch_checks_policy_before_any_runtime_call(principal, roles, tenant):
    from fastapi import HTTPException

    from ting.api.workflows import WorkflowLaunchBody, launch_workflow_execution

    workflow = SimpleNamespace(
        id=uuid4(), owner_id="alice", tenant_id="acme", scope=WorkflowScope.USER
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(authorization=CedarAuthorizationAdapter()))
    )
    factory = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await launch_workflow_execution(
            request=request,
            workflow=workflow,
            launch=WorkflowLaunchBody(prompt="Attempt unauthorized workflow launch"),
            volundr_factory=factory,
            principal=replace(principal, roles=roles, tenant_id=tenant),
        )
    assert exc.value.status_code == 403
    assert factory.mock_calls == []


async def test_tracker_run_maps_to_stored_project_not_tracker_uuid(principal):
    from ting.api.runs import _authorize_tracker_run

    repo, tracker = AsyncMock(), AsyncMock()
    tracker.get_saga_for_run.return_value = SimpleNamespace(id=uuid4(), tracker_id="project-1")
    saga = SimpleNamespace(
        id=uuid4(),
        tracker_id="project-1",
        owner_id="alice",
        tenant_id="acme",
        tracker_connection_id="",
    )
    repo.list_sagas.return_value = [saga]
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(authorization=CedarAuthorizationAdapter(), saga_repo=repo)
        ),
        method="POST",
        path_params={},
        url=SimpleNamespace(path="/api/v1/ting/runs/id/approve"),
    )
    run = SimpleNamespace(id=uuid4(), tracker_id="issue-1")
    assert await _authorize_tracker_run(request, principal, tracker, run)
    saga.tenant_id = "other"
    assert not await _authorize_tracker_run(request, principal, tracker, run)


async def test_visible_short_circuits_on_missing_item_without_checking_authorization(principal):
    repo = AsyncMock()
    repo.get_campaign.return_value = None
    authorization = AsyncMock()
    policy = AuthorizedCampaignRepository(repo, authorization, principal)
    assert await policy.get_campaign(uuid4()) is None
    authorization.is_allowed.assert_not_called()


async def test_save_check_rejects_ownership_change_even_when_update_is_allowed(principal):
    existing = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    moved = SimpleNamespace(id=existing.id, owner_id="alice", tenant_id="other-tenant")
    repo = AsyncMock()
    repo.get_campaign.return_value = existing
    policy = AuthorizedCampaignRepository(repo, AllowAllAuthorizationAdapter(), principal)
    with pytest.raises(AuthorizationDeniedError, match="Resource ownership is immutable"):
        await policy.save_campaign(moved)
    repo.save_campaign.assert_not_called()


async def test_authorized_workflow_repository_delegates_every_method(principal):
    repo = AsyncMock()
    workflow = SimpleNamespace(
        id=uuid4(), owner_id="alice", tenant_id="acme", scope=WorkflowScope.USER
    )
    repo.get_workflow.return_value = workflow
    policy = AuthorizedWorkflowRepository(repo, AllowAllAuthorizationAdapter(), principal)

    repo.list_workflow_versions.return_value = ["v1"]
    assert await policy.list_workflow_versions(workflow.id) == ["v1"]
    repo.list_workflow_versions.assert_awaited_once_with(workflow.id)

    repo.get_workflow_version.return_value = "version-doc"
    result = await policy.get_workflow_version(workflow.id, version="1.0.0", document_revision=None)
    assert result == "version-doc"
    repo.get_workflow_version.assert_awaited_once_with(
        workflow.id, version="1.0.0", document_revision=None
    )

    repo.save_workflow_version.return_value = workflow
    saved = await policy.save_workflow_version(
        workflow, expected_revision="rev-1", base_revision="rev-0", bump="minor"
    )
    assert saved is workflow
    repo.save_workflow_version.assert_awaited_once_with(
        workflow, expected_revision="rev-1", base_revision="rev-0", bump="minor"
    )

    repo.delete_workflow.return_value = None
    await policy.delete_workflow(workflow.id)
    repo.delete_workflow.assert_awaited_once_with(workflow.id)


async def test_authorized_campaign_repository_delegates_every_method(principal):
    repo = AsyncMock()
    own = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    foreign = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="other")
    policy = AuthorizedCampaignRepository(repo, CedarAuthorizationAdapter(), principal)

    repo.list_campaigns.return_value = [own, foreign]
    assert await policy.list_campaigns(owner_id="alice") == [own]

    repo.list_active_campaigns.return_value = [own, foreign]
    assert await policy.list_active_campaigns() == [own]

    repo.get_campaign_by_slug.return_value = own
    assert await policy.get_campaign_by_slug("slug", owner_id="alice") is own
    repo.get_campaign_by_slug.assert_awaited_once_with("slug", owner_id="alice")

    repo.get_campaign.return_value = own
    await policy.delete_campaign(own.id)
    repo.delete_campaign.assert_awaited_once_with(own.id)

    repo.get_campaign.return_value = foreign
    with pytest.raises(AuthorizationDeniedError):
        await policy.delete_campaign(foreign.id)
    repo.delete_campaign.assert_awaited_once()


async def test_authorized_saga_repository_slug_delete_and_updates(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)
    repo.get_saga_by_slug.return_value = saga

    assert await policy.get_saga_by_slug("slug") is saga

    repo.get_saga.return_value = saga
    await policy.delete_saga(saga.id, owner_id="alice")
    repo.delete_saga.assert_awaited_once_with(saga.id, owner_id="alice")

    repo.update_saga_status.return_value = None
    await policy.update_saga_status(saga.id, RunStatus.RUNNING)
    repo.update_saga_status.assert_awaited_once_with(saga.id, RunStatus.RUNNING)

    repo.update_saga_workflow.return_value = None
    await policy.update_saga_workflow(saga.id, workflow_id=uuid4())
    repo.update_saga_workflow.assert_awaited_once()

    repo.update_saga_target.return_value = None
    await policy.update_saga_target(saga.id, target="main")
    repo.update_saga_target.assert_awaited_once()


async def test_get_phase_get_run_and_listing_short_circuit_on_missing_parent(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    repo.get_phase.return_value = None
    assert await policy.get_phase(uuid4()) is None

    phase = SimpleNamespace(id=uuid4(), saga_id=saga.id)
    repo.get_phase.return_value = phase
    repo.get_saga.return_value = None
    assert await policy.get_phase(phase.id) is None

    repo.get_saga.return_value = saga
    assert await policy.get_phase(phase.id) is phase

    repo.get_run.return_value = None
    assert await policy.get_run(uuid4()) is None

    run = SimpleNamespace(id=uuid4(), phase_id=phase.id)
    repo.get_run.return_value = run
    repo.get_phase.return_value = None
    assert await policy.get_run(run.id) is None

    repo.get_phase.return_value = phase
    repo.get_saga.return_value = saga
    assert await policy.get_run(run.id) is run

    repo.get_phase.return_value = None
    assert await policy.get_runs_by_phase(phase.id) == []
    repo.get_saga.return_value = None
    assert await policy.get_phases_by_saga(saga.id) == []

    repo.get_phase.return_value = phase
    repo.get_saga.return_value = saga
    repo.get_runs_by_phase.return_value = [run]
    assert await policy.get_runs_by_phase(phase.id) == [run]

    repo.get_phases_by_saga.return_value = [phase]
    assert await policy.get_phases_by_saga(saga.id) == [phase]


async def test_save_phase_rejects_reparenting_and_persists_otherwise(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    repo.get_saga.return_value = saga
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    existing_phase = SimpleNamespace(id=uuid4(), saga_id=uuid4())
    repo.get_phase.return_value = existing_phase
    new_phase = SimpleNamespace(id=existing_phase.id, saga_id=uuid4())
    with pytest.raises(AuthorizationDeniedError, match="Phase parent is immutable"):
        await policy.save_phase(new_phase)
    repo.save_phase.assert_not_called()

    repo.get_phase.return_value = None
    saved_phase = SimpleNamespace(id=uuid4(), saga_id=saga.id)
    repo.save_phase.return_value = saved_phase
    assert await policy.save_phase(saved_phase, conn="conn-1") is saved_phase
    repo.save_phase.assert_awaited_once_with(saved_phase, conn="conn-1")


async def test_save_run_requires_existing_phase_and_rejects_reparenting(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    repo.get_phase.return_value = None
    orphan_run = SimpleNamespace(id=uuid4(), phase_id=uuid4())
    with pytest.raises(AuthorizationDeniedError, match="Resource operation denied"):
        await policy.save_run(orphan_run)
    repo.save_run.assert_not_called()

    phase = SimpleNamespace(id=uuid4(), saga_id=saga.id)
    repo.get_phase.return_value = phase
    repo.get_saga.return_value = saga
    existing_run = SimpleNamespace(id=uuid4(), phase_id=uuid4())
    repo.get_run.return_value = existing_run
    reparented_run = SimpleNamespace(id=existing_run.id, phase_id=phase.id)
    with pytest.raises(AuthorizationDeniedError, match="Run parent is immutable"):
        await policy.save_run(reparented_run)
    repo.save_run.assert_not_called()

    repo.get_run.return_value = None
    new_run = SimpleNamespace(id=uuid4(), phase_id=phase.id)
    repo.save_run.return_value = new_run
    assert await policy.save_run(new_run, conn="conn-1") is new_run
    repo.save_run.assert_awaited_once_with(new_run, conn="conn-1")


async def test_update_run_outcome_requires_full_ancestry(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    repo.get_run.return_value = None
    with pytest.raises(AuthorizationDeniedError):
        await policy.update_run_outcome(uuid4(), "outcome", "event", RunStatus.MERGED)
    repo.update_run_outcome.assert_not_called()

    phase = SimpleNamespace(id=uuid4(), saga_id=saga.id)
    run = SimpleNamespace(id=uuid4(), phase_id=phase.id)
    repo.get_run.return_value = run
    repo.get_phase.return_value = phase
    repo.get_saga.return_value = saga
    repo.update_run_outcome.return_value = "updated"
    result = await policy.update_run_outcome(run.id, "outcome", "event", RunStatus.MERGED)
    assert result == "updated"
    repo.update_run_outcome.assert_awaited_once_with(run.id, "outcome", "event", RunStatus.MERGED)


async def test_count_by_status_tallies_runs_across_visible_sagas(principal):
    repo = AsyncMock()
    saga = SimpleNamespace(id=uuid4(), owner_id="alice", tenant_id="acme")
    phase = SimpleNamespace(id=uuid4(), saga_id=saga.id)
    run_a = SimpleNamespace(id=uuid4(), status=RunStatus.RUNNING)
    run_b = SimpleNamespace(id=uuid4(), status=RunStatus.RUNNING)
    run_c = SimpleNamespace(id=uuid4(), status=RunStatus.MERGED)
    repo.list_sagas.return_value = [saga]
    repo.get_phases_by_saga.return_value = [phase]
    repo.get_runs_by_phase.return_value = [run_a, run_b, run_c]
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    counts = await policy.count_by_status()

    assert counts[RunStatus.RUNNING.value] == 2
    assert counts[RunStatus.MERGED.value] == 1
    assert counts[RunStatus.PENDING.value] == 0


async def test_begin_delegates_to_wrapped_repository_transaction(principal):
    @asynccontextmanager
    async def fake_begin():
        yield "conn-object"

    repo = SimpleNamespace(begin=fake_begin)
    policy = AuthorizedSagaRepository(repo, AllowAllAuthorizationAdapter(), principal)

    async with policy.begin() as conn:
        assert conn == "conn-object"
