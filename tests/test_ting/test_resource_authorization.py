"""Stored attribution, read filtering, and mutation checks using real Cedar."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import Principal
from identity.ports import AuthorizationDeniedError
from ting.domain.models import WorkflowScope
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

    from ting.api.workflows import launch_workflow_execution

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
            launch=None,
            volundr_factory=factory,
            principal=replace(principal, roles=roles, tenant_id=tenant),
        )
    assert exc.value.status_code == 403
    assert factory.mock_calls == []


async def test_tracker_run_maps_to_stored_project_not_tracker_uuid(principal):
    from ting.api.runs import _authorize_tracker_run

    repo, tracker = AsyncMock(), AsyncMock()
    tracker.get_saga_for_run.return_value = SimpleNamespace(id=uuid4(), tracker_id="project-1")
    saga = SimpleNamespace(id=uuid4(), tracker_id="project-1", owner_id="alice", tenant_id="acme")
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
