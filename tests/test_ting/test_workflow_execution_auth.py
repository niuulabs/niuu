"""Behavioural coverage for the shared workflow-execution authorization helpers.

These are the machinery both the generic and delivery routers lean on to bind
a workload JWT to the exact execution, node, and session it claims to act on.
Every raise/return branch here is a security decision, so each is exercised
directly against the functions rather than only through a full API round trip.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException

from tests.test_ting.test_workflow_execution_service import (
    InMemoryWorkflowRepository,
    _execution,
    _proposal,
)
from ting.api.workflow_execution_auth import (
    assert_child_credential_lineage,
    assert_coordinator_claims,
    assert_parent_workload_claims_if_scoped,
    resolve_launch_expansion_policy,
)
from ting.domain.models import WorkflowDefinition, WorkflowDependency, WorkflowScope
from ting.domain.workflow_execution import (
    ChildExecutionState,
    WorkflowExecutionError,
    make_children,
)

SECRET = "test-key-for-workflow-execution-auth-32-bytes"


def _settings_request(*, allow_anonymous_dev: bool = False, campaign_repo=None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    auth=SimpleNamespace(allow_anonymous_dev=allow_anonymous_dev)
                ),
                workflow_campaign_repo=campaign_repo,
            )
        )
    )


def _workflow(**changes) -> WorkflowDefinition:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    value = WorkflowDefinition(
        id=uuid4(),
        name="Editorial Translation",
        description="",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "nodes": [
                {
                    "id": "commission-translations",
                    "kind": "subworkflow",
                    "templates": {"translation-assignment": "translation-assignment"},
                    "allowedCoordinator": "editorial-coordinator",
                    "inputSchema": {"type": "object"},
                    "resultSchema": {"type": "object"},
                    "maxChildren": 10,
                    "maxAttempts": 3,
                    "maxActiveChildren": 4,
                    "joinMode": "all",
                },
                {"id": "not-a-subworkflow", "kind": "stage"},
                {
                    "id": "no-templates",
                    "kind": "subworkflow",
                },
                {
                    "id": "undeclared-dependency",
                    "kind": "subworkflow",
                    "templates": {"ghost": "ghost-alias"},
                },
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
        schema_version=2,
        workflow_dependencies={
            "translation-assignment": WorkflowDependency(
                id=uuid4(), revision="v1", digest="sha256:" + "b" * 64
            ),
        },
    )
    return replace(value, **changes)


class TestResolveLaunchExpansionPolicy:
    def test_resolves_node_and_policy(self) -> None:
        workflow = _workflow()

        node, policy = resolve_launch_expansion_policy(workflow, "commission-translations")

        assert node["id"] == "commission-translations"
        assert policy.coordinator_id == "editorial-coordinator"
        assert policy.templates["translation-assignment"].dependency_alias == (
            "translation-assignment"
        )

    def test_raises_when_node_is_missing(self) -> None:
        workflow = _workflow()

        with pytest.raises(WorkflowExecutionError) as excinfo:
            resolve_launch_expansion_policy(workflow, "no-such-node")

        assert "does not declare a subworkflow node" in str(excinfo.value)

    def test_raises_when_node_is_not_a_subworkflow(self) -> None:
        workflow = _workflow()

        with pytest.raises(WorkflowExecutionError) as excinfo:
            resolve_launch_expansion_policy(workflow, "not-a-subworkflow")

        assert "does not declare a subworkflow node" in str(excinfo.value)

    def test_raises_when_node_declares_no_templates(self) -> None:
        workflow = _workflow()

        with pytest.raises(WorkflowExecutionError) as excinfo:
            resolve_launch_expansion_policy(workflow, "no-templates")

        assert "declares no templates" in str(excinfo.value)

    def test_raises_when_dependency_is_undeclared(self) -> None:
        workflow = _workflow()

        with pytest.raises(WorkflowExecutionError) as excinfo:
            resolve_launch_expansion_policy(workflow, "undeclared-dependency")

        assert "undeclared workflow dependency" in str(excinfo.value)


def _coordinator_claims(execution) -> dict:
    return {
        "token_use": "valkyrie_build",
        "workload_workflow_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_parent_session_key": f"workflow:execution-{execution.id.hex}",
        "workload_coordinator_id": execution.policy.coordinator_id,
        "workload_forge_session_id": execution.parent_session_id,
        "scopes": ["ting:workflow:coordinate"],
    }


class TestAssertCoordinatorClaims:
    def test_allows_anonymous_dev_with_no_bearer_token(self) -> None:
        execution = _execution()
        request = _settings_request(allow_anonymous_dev=True)

        assert assert_coordinator_claims(request, None, execution) is None

    def test_raises_when_token_is_not_valid_jwt(self) -> None:
        execution = _execution()
        request = _settings_request()

        with pytest.raises(HTTPException) as excinfo:
            assert_coordinator_claims(request, "not-a-jwt", execution)

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Coordinator credential is invalid"

    def test_raises_when_claims_do_not_match_execution(self) -> None:
        execution = _execution()
        request = _settings_request()
        claims = {**_coordinator_claims(execution), "workload_coordinator_id": "someone-else"}
        token = jwt.encode(claims, SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as excinfo:
            assert_coordinator_claims(request, token, execution)

        assert excinfo.value.status_code == 403
        assert "not bound to this execution node" in excinfo.value.detail

    def test_raises_when_scope_is_missing(self) -> None:
        execution = _execution()
        request = _settings_request()
        claims = {**_coordinator_claims(execution), "scopes": []}
        token = jwt.encode(claims, SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as excinfo:
            assert_coordinator_claims(request, token, execution)

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Coordinator credential scope is missing"

    def test_accepts_a_correctly_bound_token(self) -> None:
        execution = _execution()
        request = _settings_request()
        token = jwt.encode(_coordinator_claims(execution), SECRET, algorithm="HS256")

        assert assert_coordinator_claims(request, token, execution) is None


class TestAssertParentWorkloadClaimsIfScoped:
    def test_returns_when_token_is_not_valid_jwt(self) -> None:
        execution = _execution()
        request = _settings_request()

        assert assert_parent_workload_claims_if_scoped(request, "not-a-jwt", execution) is None

    def test_returns_when_token_use_is_not_scoped(self) -> None:
        execution = _execution()
        request = _settings_request()
        token = jwt.encode({"token_use": "pat"}, SECRET, algorithm="HS256")

        assert assert_parent_workload_claims_if_scoped(request, token, execution) is None

    def test_delegates_to_coordinator_claims_when_scoped(self) -> None:
        execution = _execution()
        request = _settings_request()
        token = jwt.encode(
            {**_coordinator_claims(execution), "workload_coordinator_id": "someone-else"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            assert_parent_workload_claims_if_scoped(request, token, execution)

        assert excinfo.value.status_code == 403

    def test_returns_when_token_is_none(self) -> None:
        execution = _execution()
        request = _settings_request()

        assert assert_parent_workload_claims_if_scoped(request, None, execution) is None


def _child_setup():
    execution = _execution(state=execution_state_running(), current_generation=1)
    repository = InMemoryWorkflowRepository(execution)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "child-a"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="a2a-task-1",
    )
    repository.children = [child]
    return execution, repository, child


def execution_state_running():
    from ting.domain.workflow_execution import ExecutionState

    return ExecutionState.RUNNING


def _child_claims(execution, child) -> dict:
    return {
        "token_use": "valkyrie_build",
        "workload_workflow_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_coordinator_id": execution.policy.coordinator_id,
        "scopes": ["ting:workflow:coordinate"],
        "workload_parent_session_key": "workflow:a2a-child",
        "workload_sub": "workflow:a2a-child",
        "workload_child_attempt_id": str(child.id),
        "workload_child_task_id": child.task_id,
        "workload_forge_session_id": "child-session-1",
    }


class TestAssertChildCredentialLineage:
    @pytest.mark.asyncio
    async def test_allows_anonymous_dev_with_no_bearer_token(self) -> None:
        execution, repository, _child = _child_setup()
        request = _settings_request(allow_anonymous_dev=True)

        result = await assert_child_credential_lineage(
            request,
            None,
            execution,
            repository,
            operation="message",
            allowed_child_operations=frozenset({"message"}),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_raises_when_token_is_not_valid_jwt(self) -> None:
        execution, repository, _child = _child_setup()
        request = _settings_request()

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                "not-a-jwt",
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child credential is invalid"

    @pytest.mark.asyncio
    async def test_raises_when_common_lineage_claims_are_wrong(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        token = jwt.encode(
            {**_child_claims(execution, child), "workload_coordinator_id": "someone-else"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child credential lineage is invalid"

    @pytest.mark.asyncio
    async def test_raises_when_scope_is_missing(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        token = jwt.encode(
            {**_child_claims(execution, child), "scopes": []}, SECRET, algorithm="HS256"
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child credential lineage is invalid"

    @pytest.mark.asyncio
    async def test_parent_bound_credential_is_accepted(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        parent_key = f"workflow:execution-{execution.id.hex}"
        token = jwt.encode(
            {
                **_child_claims(execution, child),
                "workload_parent_session_key": parent_key,
                "workload_forge_session_id": execution.parent_session_id,
            },
            SECRET,
            algorithm="HS256",
        )

        result = await assert_child_credential_lineage(
            request,
            token,
            execution,
            repository,
            operation="message",
            allowed_child_operations=frozenset({"message"}),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_parent_bound_credential_rejected_when_session_mismatch(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        parent_key = f"workflow:execution-{execution.id.hex}"
        token = jwt.encode(
            {
                **_child_claims(execution, child),
                "workload_parent_session_key": parent_key,
                "workload_forge_session_id": "wrong-session",
            },
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert "not bound to the parent Forge session" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_raises_when_operation_is_not_allowed_for_a_child(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        token = jwt.encode(_child_claims(execution, child), SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="cancel",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert "cannot authorize operation cancel" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_raises_when_attempt_id_is_not_a_valid_uuid(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        token = jwt.encode(
            {**_child_claims(execution, child), "workload_child_attempt_id": "not-a-uuid"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child attempt is invalid"

    @pytest.mark.asyncio
    async def test_raises_when_child_lineage_does_not_match(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request()
        token = jwt.encode(
            {**_child_claims(execution, child), "workload_child_task_id": "wrong-task"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child lineage is invalid"

    @pytest.mark.asyncio
    async def test_raises_when_campaign_does_not_match(self) -> None:
        execution, repository, child = _child_setup()

        class CampaignRepo:
            async def get_campaign_by_slug(self, slug, *, owner_id=None):
                return SimpleNamespace(tenant_id=execution.tenant_id, session_id="child-session-1")

        request = _settings_request(campaign_repo=CampaignRepo())
        token = jwt.encode(
            {**_child_claims(execution, child), "workload_forge_session_id": "wrong-session"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert "not bound to the child Forge session" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_raises_when_no_campaign_repository_is_configured(self) -> None:
        execution, repository, child = _child_setup()
        request = _settings_request(campaign_repo=None)
        token = jwt.encode(_child_claims(execution, child), SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert "not bound to the child Forge session" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_accepts_a_correctly_bound_active_child_credential(self) -> None:
        execution, repository, child = _child_setup()

        class CampaignRepo:
            async def get_campaign_by_slug(self, slug, *, owner_id=None):
                assert slug == child.task_id
                return SimpleNamespace(tenant_id=execution.tenant_id, session_id="child-session-1")

        request = _settings_request(campaign_repo=CampaignRepo())
        token = jwt.encode(_child_claims(execution, child), SECRET, algorithm="HS256")

        result = await assert_child_credential_lineage(
            request,
            token,
            execution,
            repository,
            operation="message",
            allowed_child_operations=frozenset({"message"}),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_raises_when_attempt_is_no_longer_the_active_attempt(self) -> None:
        execution, repository, child = _child_setup()
        newer_attempt = replace(child, id=uuid4(), attempt=child.attempt + 1)
        repository.children = [child, newer_attempt]

        class CampaignRepo:
            async def get_campaign_by_slug(self, slug, *, owner_id=None):
                return SimpleNamespace(tenant_id=execution.tenant_id, session_id="child-session-1")

        request = _settings_request(campaign_repo=CampaignRepo())
        token = jwt.encode(_child_claims(execution, child), SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as excinfo:
            await assert_child_credential_lineage(
                request,
                token,
                execution,
                repository,
                operation="message",
                allowed_child_operations=frozenset({"message"}),
            )

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Child attempt is no longer active"
