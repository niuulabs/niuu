"""Tests for the A2A task endpoint over workflow launches."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.api.a2a import create_a2a_router
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import AuthConfig, Settings
from ting.domain.models import (
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.ports.volundr import SpawnRequest, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

A2A_PATH = "/api/v1/ting/a2a"
_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        return list(self._workflows.values())

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


class InMemoryCampaignRepository(WorkflowCampaignRepository):
    def __init__(self, campaigns: list[WorkflowCampaign] | None = None) -> None:
        self._campaigns = {campaign.id: campaign for campaign in campaigns or []}

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        return [c for c in self._campaigns.values() if c.owner_id == owner_id]

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            c
            for c in self._campaigns.values()
            if c.status not in (WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED)
        ]

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        return self._campaigns.get(campaign_id)

    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        for campaign in self._campaigns.values():
            if campaign.slug != slug:
                continue
            if owner_id is not None and campaign.owner_id != owner_id:
                return None
            return campaign
        return None

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self._campaigns[campaign.id] = campaign
        return campaign

    async def delete_campaign(self, campaign_id: UUID) -> bool:
        return self._campaigns.pop(campaign_id, None) is not None


class RecordingVolundrPort(VolundrPort):
    def __init__(self, *, session_status: str = "starting") -> None:
        self._session_status = session_status
        self.spawned: list[SpawnRequest] = []
        self.stopped: list[str] = []

    @property
    def name(self) -> str:
        return "local"

    @property
    def target_id(self) -> str:
        return "local"

    @property
    def tags(self) -> list[str]:
        return []

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        self.spawned.append(request)
        return VolundrSession(
            id="session-123",
            name=request.name,
            status=self._session_status,
            chat_endpoint="wss://sessions.example/s/session-123/session",
            tracker_issue_id=request.tracker_issue_id,
            cluster_name="local",
            repo=request.repo,
            branch=request.branch,
            base_branch=request.base_branch,
            workload_type=request.workload_type,
        )

    async def get_session(self, session_id: str, *, auth_token=None, principal=None):
        return None

    async def list_sessions(self, *, auth_token=None, principal=None):
        return []

    async def get_pr_status(self, session_id: str):
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        raise NotImplementedError

    async def send_message(self, session_id: str, message: str, *, auth_token=None, principal=None):
        raise NotImplementedError

    async def send_directed_room_message(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    async def get_workflow_gates(self, session_id: str, *, auth_token=None, principal=None):
        return []

    async def resolve_workflow_gate(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    async def stop_session(self, session_id: str, *, auth_token=None, principal=None) -> None:
        self.stopped.append(session_id)

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        return []

    async def list_repos(self, *, auth_token=None, principal=None):
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        raise NotImplementedError

    async def get_conversation(self, session_id: str) -> dict:
        raise NotImplementedError

    async def subscribe_activity(self):
        if False:  # pragma: no cover
            yield None


class RecordingVolundrFactory:
    def __init__(self, adapters: list[VolundrPort]) -> None:
        self._adapters = adapters

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return list(self._adapters)

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._adapters[0] if self._adapters else None

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return list(self._adapters)


def _make_workflow(*, name: str = "tool-builder") -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name=name,
        description="Builds a learned tool.",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["tool-builder"],
            "nodes": [
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Build",
                    "stageMembers": [{"personaId": "tool-smith", "model": "gpt-5.5"}],
                }
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
    )


def _make_campaign(
    *,
    slug: str = "task-1",
    owner_id: str = "user-1",
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    metadata: dict[str, Any] | None = None,
) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug=slug,
        name=slug,
        owner_id=owner_id,
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="tool-builder",
        workflow_snapshot={"graph": {"nodes": [], "edges": []}},
        session_id="session-123",
        session_name=slug,
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata={"surface": "a2a", **(metadata or {})},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )


def _headers(*, user_id: str = "user-1", token: str | None = None) -> dict[str, str]:
    headers = {
        "x-auth-user-id": user_id,
        "x-auth-roles": "product:user",
        "A2A-Version": "1.0",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_token(scopes: list[str]) -> str:
    return jwt.encode(
        {"sub": "user-1", "token_use": "valkyrie_build", "scopes": scopes},
        _SIGNING_KEY,
        algorithm="HS256",
    )


def _make_client(
    *,
    workflow_repo: WorkflowRepository | None = None,
    campaign_repo: WorkflowCampaignRepository | None = None,
    volundr: RecordingVolundrPort | None = None,
) -> tuple[TestClient, InMemoryCampaignRepository, RecordingVolundrPort]:
    workflow_repo = workflow_repo or InMemoryWorkflowRepository()
    campaigns = campaign_repo or InMemoryCampaignRepository()
    port = volundr or RecordingVolundrPort()
    app = FastAPI()
    app.include_router(create_a2a_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaigns
    app.dependency_overrides[resolve_volundr_factory] = lambda: RecordingVolundrFactory([port])
    return TestClient(app), campaigns, port


def _rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        A2A_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=headers or _headers(),
    )


def _send_params(workflow_id: str, *, prompt: str = "Build the widget tool") -> dict[str, Any]:
    return {
        "message": {
            "messageId": "msg-1",
            "role": "ROLE_USER",
            "parts": [{"text": prompt}],
            "metadata": {"workflowId": workflow_id, "model": "gpt-5.5"},
        }
    }


class TestSendMessage:
    def test_launches_workflow_and_returns_submitted_task(self) -> None:
        workflow = _make_workflow()
        client, campaigns, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )

        response = _rpc(client, "SendMessage", _send_params(str(workflow.id)))

        assert response.status_code == 200
        result = response.json()["result"]["task"]
        assert result["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert result["metadata"]["workflowId"] == str(workflow.id)
        assert result["metadata"]["sessionId"] == "session-123"

        assert len(port.spawned) == 1
        assert port.spawned[0].workload_type == "ravn_flock"

        import asyncio

        campaign = asyncio.run(campaigns.get_campaign_by_slug(result["id"]))
        assert campaign is not None
        assert campaign.owner_id == "user-1"
        assert campaign.metadata["surface"] == "a2a"

    def test_missing_workflow_id_is_invalid_params(self) -> None:
        client, _, _ = _make_client()
        params = _send_params(str(uuid4()))
        del params["message"]["metadata"]["workflowId"]

        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 200
        error = response.json()["error"]
        assert error["code"] == -32602
        assert "workflowId" in error["message"]

    def test_unknown_workflow_is_invalid_params(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "SendMessage", _send_params(str(uuid4())))

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "unknown workflow" in error["message"]

    def test_empty_prompt_is_invalid_params(self) -> None:
        workflow = _make_workflow()
        client, _, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        params = _send_params(str(workflow.id))
        params["message"]["parts"] = [{"text": "   "}]

        response = _rpc(client, "SendMessage", params)

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "text part" in error["message"]

    def test_build_token_without_launch_scope_is_403(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        token = _build_token(["forge:session:create"])

        response = _rpc(
            client,
            "SendMessage",
            _send_params(str(workflow.id)),
            headers=_headers(token=token),
        )

        assert response.status_code == 403
        assert port.spawned == []

    def test_build_token_with_launch_scope_is_admitted(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        token = _build_token(["ting:workflow:launch"])

        response = _rpc(
            client,
            "SendMessage",
            _send_params(str(workflow.id)),
            headers=_headers(token=token),
        )

        assert response.status_code == 200
        assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert len(port.spawned) == 1

    def test_human_token_is_unaffected_by_scope_check(self) -> None:
        workflow = _make_workflow()
        client, _, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        token = jwt.encode({"type": "pat", "sub": "user-1"}, _SIGNING_KEY, algorithm="HS256")

        response = _rpc(
            client,
            "SendMessage",
            _send_params(str(workflow.id)),
            headers=_headers(token=token),
        )

        assert response.status_code == 200
        assert "result" in response.json()

    def test_continuation_with_task_id_is_unsupported(self) -> None:
        workflow = _make_workflow()
        client, _, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        params = _send_params(str(workflow.id))
        params["message"]["taskId"] = "task-1"

        response = _rpc(client, "SendMessage", params)

        error = response.json()["error"]
        assert "continuation" in error["message"]


class TestGetTask:
    @pytest.mark.parametrize(
        ("campaign_status", "expected_state"),
        [
            (WorkflowCampaignStatus.PENDING, "TASK_STATE_SUBMITTED"),
            (WorkflowCampaignStatus.RUNNING, "TASK_STATE_WORKING"),
            (WorkflowCampaignStatus.BLOCKED, "TASK_STATE_INPUT_REQUIRED"),
            (WorkflowCampaignStatus.COMPLETED, "TASK_STATE_COMPLETED"),
            (WorkflowCampaignStatus.FAILED, "TASK_STATE_FAILED"),
        ],
    )
    def test_maps_campaign_status_to_task_state(
        self,
        campaign_status: WorkflowCampaignStatus,
        expected_state: str,
    ) -> None:
        campaign = _make_campaign(status=campaign_status)
        client, _, _ = _make_client(campaign_repo=InMemoryCampaignRepository([campaign]))

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        result = response.json()["result"]
        assert result["id"] == campaign.slug
        assert result["status"]["state"] == expected_state

    def test_unknown_task_is_not_found(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "GetTask", {"id": "missing"})

        error = response.json()["error"]
        assert "no task" in error["message"]

    def test_other_owners_task_is_not_found(self) -> None:
        campaign = _make_campaign(owner_id="user-2")
        client, _, _ = _make_client(campaign_repo=InMemoryCampaignRepository([campaign]))

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        assert "error" in response.json()


class TestCancelTask:
    def test_cancel_stops_session_and_reports_canceled(self) -> None:
        campaign = _make_campaign(status=WorkflowCampaignStatus.RUNNING)
        client, campaigns, port = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
        )

        response = _rpc(client, "CancelTask", {"id": campaign.slug})

        result = response.json()["result"]
        assert result["status"]["state"] == "TASK_STATE_CANCELED"
        assert port.stopped == ["session-123"]

        followup = _rpc(client, "GetTask", {"id": campaign.slug})
        assert followup.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"

    def test_cancel_terminal_task_is_not_cancelable(self) -> None:
        campaign = _make_campaign(status=WorkflowCampaignStatus.COMPLETED)
        client, _, port = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
        )

        response = _rpc(client, "CancelTask", {"id": campaign.slug})

        error = response.json()["error"]
        assert "terminal" in error["message"]
        assert port.stopped == []


class TestProtocolSurface:
    def test_unknown_method_is_method_not_found(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "DoSomethingElse", {})

        error = response.json()["error"]
        assert error["code"] == -32601

    def test_list_tasks_is_unsupported(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "ListTasks", {})

        assert "error" in response.json()

    def test_missing_identity_headers_are_unauthorized(self) -> None:
        client, _, _ = _make_client()

        response = client.post(
            A2A_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}},
        )

        assert response.status_code == 401
