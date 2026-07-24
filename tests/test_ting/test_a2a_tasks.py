"""Tests for the A2A task endpoint over workflow launches."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.api.a2a import create_a2a_router
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import create_research_router, resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig, AuthConfig, Settings
from ting.domain.models import (
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
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
        workflows = list(self._workflows.values())
        if scope == WorkflowScope.SYSTEM:
            return [workflow for workflow in workflows if workflow.scope == WorkflowScope.SYSTEM]
        if scope == WorkflowScope.USER:
            return [
                workflow
                for workflow in workflows
                if workflow.scope == WorkflowScope.USER and workflow.owner_id == owner_id
            ]
        return [
            workflow
            for workflow in workflows
            if workflow.scope == WorkflowScope.SYSTEM or workflow.owner_id == owner_id
        ]

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


class FailingCampaignRepository(InMemoryCampaignRepository):
    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        raise RuntimeError("campaign persistence failed")


class RecordingVolundrPort(VolundrPort):
    def __init__(self, *, session_status: str = "starting") -> None:
        self._session_status = session_status
        self.spawned: list[SpawnRequest] = []
        self.stopped: list[str] = []
        self.gates: list[dict] = []
        self.resolved_gates: list[tuple[str, str, str, str, str]] = []
        self.help_requests: list[dict] = []
        self.answered_help: list[tuple[str, str, str, str]] = []

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
        return list(self.gates)

    async def get_help_requests(self, session_id: str, *, auth_token=None, principal=None):
        return list(self.help_requests)

    async def answer_help_request(
        self,
        session_id: str,
        request_id: str,
        answer: str,
        *,
        source: str = "ting",
        auth_token=None,
        principal=None,
    ) -> dict:
        self.answered_help.append((session_id, request_id, answer, source))
        return {"status": "answered", "message_id": "msg-1"}

    async def resolve_workflow_gate(
        self,
        session_id: str,
        gate_id: str,
        decision: str,
        *,
        notes: str = "",
        source: str = "ting",
        auth_token=None,
        principal=None,
    ) -> dict:
        self.resolved_gates.append((session_id, gate_id, decision, notes, source))
        return {"status": "resolved"}

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
        self.connection_calls: list[str] = []

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return list(self._adapters)

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._adapters[0] if self._adapters else None

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        self.connection_calls.append(connection_id)
        for adapter in self._adapters:
            if connection_id in {
                getattr(adapter, "target_id", None),
                getattr(adapter, "name", None),
            }:
                return adapter
        return None

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
            "artifactPaths": ["capabilities/{slug}/learned_tool.json"],
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
    workflow_snapshot: dict[str, Any] | None = None,
    connection_id: str | None = None,
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
        workflow_snapshot=workflow_snapshot or {"graph": {"nodes": [], "edges": []}},
        session_id="session-123",
        session_name=slug,
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata={"surface": "a2a", **(metadata or {})},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        connection_id=connection_id,
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
    settings: Settings | None = None,
) -> tuple[TestClient, InMemoryCampaignRepository, RecordingVolundrPort]:
    workflow_repo = workflow_repo or InMemoryWorkflowRepository()
    campaigns = campaign_repo or InMemoryCampaignRepository()
    port = volundr or RecordingVolundrPort()
    app = FastAPI()
    app.include_router(create_a2a_router())
    app.include_router(create_research_router())
    app.state.settings = settings or Settings(auth=AuthConfig(allow_anonymous_dev=False))
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
        params = _send_params(str(workflow.id))
        params["message"]["metadata"]["traceContext"] = {
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "tracestate": "niuu=resident",
            "ignored": "not-w3c",
        }

        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 200
        result = response.json()["result"]["task"]
        assert result["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert result["metadata"]["workflowId"] == str(workflow.id)
        assert result["metadata"]["sessionId"] == "session-123"

        assert len(port.spawned) == 1
        assert port.spawned[0].workload_type == "ravn_flock"
        assert port.spawned[0].workload_config["provenance"]["trace_context"] == {
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "tracestate": "niuu=resident",
        }

        import asyncio

        campaign = asyncio.run(campaigns.get_campaign_by_slug(result["id"]))
        assert campaign is not None
        assert campaign.owner_id == "user-1"
        assert campaign.metadata["surface"] == "a2a"
        assert campaign.metadata["a2a_message_id"] == "msg-1"
        assert campaign.metadata["a2a_workflow_slug"] == "build-the-widget-tool"

    def test_reuses_task_when_message_is_retried(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        params = _send_params(str(workflow.id))
        params["message"]["contextId"] = "resident-operation-1"

        first = _rpc(client, "SendMessage", params).json()["result"]["task"]
        retried = _rpc(client, "SendMessage", params).json()["result"]["task"]

        assert retried["id"] == first["id"]
        assert retried["contextId"] == "resident-operation-1"
        assert len(port.spawned) == 1

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

    def test_persistence_failure_stops_launched_session(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            campaign_repo=FailingCampaignRepository(),
        )

        response = _rpc(client, "SendMessage", _send_params(str(workflow.id)))

        assert "error" in response.json()
        assert port.stopped == ["session-123"]


class TestGateContinuation:
    @staticmethod
    def _reply_params(
        task_id: str,
        *,
        decision: str | None = "approve",
        text: str = "LGTM",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if decision is not None:
            metadata["gateDecision"] = decision
        return {
            "message": {
                "messageId": "msg-2",
                "role": "ROLE_USER",
                "taskId": task_id,
                "parts": [{"text": text}] if text else [],
                "metadata": metadata,
            }
        }

    def _blocked_client(
        self,
        *,
        gates: list[dict] | None = None,
        status: WorkflowCampaignStatus = WorkflowCampaignStatus.BLOCKED,
    ) -> tuple[TestClient, WorkflowCampaign, RecordingVolundrPort]:
        campaign = _make_campaign(status=status)
        port = RecordingVolundrPort()
        port.gates = (
            gates
            if gates is not None
            else [{"id": "gate-1", "nodeId": "review", "status": "pending"}]
        )
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            volundr=port,
        )
        return client, campaign, port

    def test_approve_resolves_gate_and_resumes_task(self) -> None:
        client, campaign, port = self._blocked_client()

        response = _rpc(client, "SendMessage", self._reply_params(campaign.slug))

        result = response.json()["result"]["task"]
        assert result["status"]["state"] == "TASK_STATE_WORKING"
        assert port.resolved_gates == [("session-123", "gate-1", "APPROVE", "LGTM", "a2a")]

    def test_request_changes_sends_notes(self) -> None:
        client, campaign, port = self._blocked_client()

        response = _rpc(
            client,
            "SendMessage",
            self._reply_params(campaign.slug, decision="request_changes", text="Fix the tests"),
        )

        assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
        assert port.resolved_gates == [
            ("session-123", "gate-1", "CHANGES_REQUESTED", "Fix the tests", "a2a")
        ]

    def test_request_changes_without_notes_is_invalid(self) -> None:
        client, campaign, port = self._blocked_client()

        response = _rpc(
            client,
            "SendMessage",
            self._reply_params(campaign.slug, decision="request_changes", text=""),
        )

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "review notes" in error["message"]
        assert port.resolved_gates == []

    def test_decisionless_reply_without_pending_question_is_rejected(self) -> None:
        # No gateDecision routes the reply to the question path; with only a
        # gate pending (no peer question) the reply has nothing to answer.
        client, campaign, _ = self._blocked_client()

        response = _rpc(client, "SendMessage", self._reply_params(campaign.slug, decision=None))

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "no pending question" in error["message"]

    def test_reply_on_non_blocked_task_is_rejected(self) -> None:
        client, campaign, port = self._blocked_client(status=WorkflowCampaignStatus.RUNNING)

        response = _rpc(client, "SendMessage", self._reply_params(campaign.slug))

        error = response.json()["error"]
        assert "not awaiting input" in error["message"]
        assert port.resolved_gates == []

    def test_reply_without_pending_gate_is_rejected(self) -> None:
        client, campaign, port = self._blocked_client(
            gates=[{"id": "gate-1", "nodeId": "review", "status": "resolved"}],
        )

        response = _rpc(client, "SendMessage", self._reply_params(campaign.slug))

        error = response.json()["error"]
        assert "no pending gate" in error["message"]
        assert port.resolved_gates == []

    def test_gate_id_metadata_disambiguates(self) -> None:
        client, campaign, port = self._blocked_client(
            gates=[
                {"id": "gate-1", "nodeId": "lint", "status": "pending"},
                {"id": "gate-2", "nodeId": "review", "status": "pending"},
            ],
        )
        params = self._reply_params(campaign.slug)
        params["message"]["metadata"]["gateId"] = "gate-2"

        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 200
        assert port.resolved_gates[0][1] == "gate-2"


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

    def test_input_required_task_carries_pending_gate_context(self) -> None:
        campaign = _make_campaign(status=WorkflowCampaignStatus.BLOCKED)
        port = RecordingVolundrPort()
        port.gates = [
            {
                "id": "gate-1",
                "node_id": "capability-spec-gate",
                "status": "pending",
                "label": "Confirm capability specification",
                "condition": "The framed spec must be confirmed.",
                "instructions": "Approve when the spec captures the tool.",
                "summary": "",
            },
            {"id": "gate-0", "node_id": "old-gate", "status": "resolved"},
        ]
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            volundr=port,
        )

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        task = response.json()["result"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert task["metadata"]["pendingGates"] == [
            {
                "gateId": "gate-1",
                "nodeId": "capability-spec-gate",
                "label": "Confirm capability specification",
                "condition": "The framed spec must be confirmed.",
                "instructions": "Approve when the spec captures the tool.",
                "summary": "",
            }
        ]

    def test_working_task_carries_no_pending_gates(self) -> None:
        campaign = _make_campaign(status=WorkflowCampaignStatus.RUNNING)
        port = RecordingVolundrPort()
        port.gates = [{"id": "gate-1", "node_id": "n", "status": "pending"}]
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            volundr=port,
        )

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        assert "pendingGates" not in response.json()["result"].get("metadata", {})


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

    def test_cancel_does_not_retarget_a_vanished_connection(self) -> None:
        campaign = _make_campaign(connection_id="connection-gone")
        client, _, primary = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
        )

        response = _rpc(client, "CancelTask", {"id": campaign.slug})

        assert response.status_code == 503
        assert primary.stopped == []


class TestProtocolSurface:
    def test_unknown_method_is_method_not_found(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "DoSomethingElse", {})

        error = response.json()["error"]
        assert error["code"] == -32601

    def test_list_tasks_filters_owned_tasks_by_context(self) -> None:
        mine = _make_campaign(
            slug="task-mine",
            metadata={"a2a_context_id": "resident-operation-1"},
        )
        other_context = _make_campaign(
            slug="task-other-context",
            metadata={"a2a_context_id": "resident-operation-2"},
        )
        other_owner = _make_campaign(
            slug="task-other-owner",
            owner_id="user-2",
            metadata={"a2a_context_id": "resident-operation-1"},
        )
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([mine, other_context, other_owner])
        )

        response = _rpc(
            client,
            "ListTasks",
            {"contextId": "resident-operation-1"},
        )

        result = response.json()["result"]
        assert [task["id"] for task in result["tasks"]] == ["task-mine"]
        assert result["totalSize"] == 1

    def test_missing_identity_headers_are_unauthorized(self) -> None:
        client, _, _ = _make_client()

        response = client.post(
            A2A_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}},
        )

        assert response.status_code == 401


class TestExtendedAgentCard:
    def test_extended_card_includes_callers_user_workflows(self) -> None:
        system = _make_workflow(name="tool-builder")
        now = datetime.now(UTC)
        mine = WorkflowDefinition(
            id=uuid4(),
            name="my-private-flow",
            description="Mine",
            version="draft",
            scope=WorkflowScope.USER,
            owner_id="user-1",
            graph={"tags": [], "nodes": [], "edges": []},
            created_at=now,
            updated_at=now,
        )
        theirs = WorkflowDefinition(
            id=uuid4(),
            name="their-private-flow",
            description="Theirs",
            version="draft",
            scope=WorkflowScope.USER,
            owner_id="user-2",
            graph={"tags": [], "nodes": [], "edges": []},
            created_at=now,
            updated_at=now,
        )
        client, _, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([system, mine, theirs]),
        )

        response = _rpc(client, "GetExtendedAgentCard", {})

        assert response.status_code == 200
        card = response.json()["result"]
        names = {skill["name"] for skill in card["skills"]}
        assert names == {"tool-builder", "my-private-flow"}
        assert card["capabilities"]["extendedAgentCard"] is True


class TestCodeOutputPointers:
    def test_repo_and_branch_surface_on_task_metadata(self) -> None:
        workflow = _make_workflow()
        client, _, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        params = _send_params(str(workflow.id))
        params["message"]["metadata"]["repo"] = "https://github.com/niuulabs/volundr.git"
        params["message"]["metadata"]["branch"] = "feat/widget"

        response = _rpc(client, "SendMessage", params)

        task = response.json()["result"]["task"]
        assert task["metadata"]["repo"] == "https://github.com/niuulabs/volundr.git"
        assert task["metadata"]["branch"] == "feat/widget"

        followup = _rpc(client, "GetTask", {"id": task["id"]})
        assert followup.json()["result"]["metadata"]["branch"] == "feat/widget"


def _mimir_workflow(root: Path) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="tool-builder",
        description="Builds a learned tool.",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["tool-builder"],
            "artifactPaths": ["capabilities/{slug}/learned_tool.json"],
            "nodes": [
                {
                    "id": "memory",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "mount_name": "local",
                    "path": str(root),
                },
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Build",
                    "stageMembers": [{"personaId": "tool-smith", "model": "gpt-5.5"}],
                },
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
    )


class TestTaskArtifacts:
    _SLUG = "build-widget"
    _JSON_PATH = "capabilities/build-widget/learned_tool.json"
    _JSON_CONTENT = '{"manifest": {"name": "widget"}}'

    def _client_with_files(
        self,
        tmp_path: Path,
        *,
        status: WorkflowCampaignStatus = WorkflowCampaignStatus.COMPLETED,
        inline_max: int = 65536,
    ) -> tuple[TestClient, WorkflowCampaign]:
        workflow = _mimir_workflow(tmp_path)
        campaign = _make_campaign(
            slug=self._SLUG,
            status=status,
            metadata={"a2a_workflow_slug": self._SLUG},
            workflow_snapshot=build_workflow_snapshot(workflow),
        )
        artifact_dir = tmp_path / "wiki" / "capabilities" / self._SLUG
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "learned_tool.json").write_text(self._JSON_CONTENT, encoding="utf-8")
        settings = Settings(
            auth=AuthConfig(allow_anonymous_dev=False),
            a2a=A2AConfig(inline_artifact_max_chars=inline_max),
        )
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            settings=settings,
        )
        return client, campaign

    def test_completed_task_inlines_small_artifacts(self, tmp_path: Path) -> None:
        client, campaign = self._client_with_files(tmp_path)

        result = _rpc(client, "GetTask", {"id": campaign.slug}).json()["result"]

        artifacts = {artifact["artifactId"]: artifact for artifact in result["artifacts"]}
        assert self._JSON_PATH in artifacts
        json_part = artifacts[self._JSON_PATH]["parts"][0]
        assert json_part["text"] == self._JSON_CONTENT
        assert json_part["mediaType"] == "application/json"
        assert json_part["filename"] == "learned_tool.json"

    def test_large_artifact_becomes_fetchable_url_part(self, tmp_path: Path) -> None:
        client, campaign = self._client_with_files(tmp_path, inline_max=10)

        result = _rpc(client, "GetTask", {"id": campaign.slug}).json()["result"]

        part = {a["artifactId"]: a for a in result["artifacts"]}[self._JSON_PATH]["parts"][0]
        assert "text" not in part
        assert "/api/v1/ting/research/campaigns/build-widget/artifact?path=" in part["url"]

        fetched = client.get(
            part["url"].removeprefix("http://testserver"),
            headers=_headers(),
        )
        assert fetched.status_code == 200
        assert fetched.json()["content"] == self._JSON_CONTENT

    def test_running_task_exposes_no_artifacts(self, tmp_path: Path) -> None:
        client, campaign = self._client_with_files(
            tmp_path,
            status=WorkflowCampaignStatus.RUNNING,
        )

        result = _rpc(client, "GetTask", {"id": campaign.slug}).json()["result"]

        assert result.get("artifacts", []) == []

    def test_unsafe_configured_artifact_path_is_ignored(self, tmp_path: Path) -> None:
        workflow = _mimir_workflow(tmp_path)
        workflow.graph["artifactPaths"] = ["../private.json"]
        campaign = _make_campaign(
            slug=self._SLUG,
            status=WorkflowCampaignStatus.COMPLETED,
            workflow_snapshot=build_workflow_snapshot(workflow),
        )
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            settings=Settings(auth=AuthConfig(allow_anonymous_dev=False)),
        )

        result = _rpc(client, "GetTask", {"id": campaign.slug}).json()["result"]

        assert result.get("artifacts", []) == []


class TestQuestionReplies:
    """Genuine peer questions (help_needed) answered over A2A — no gateDecision."""

    _QUESTION = {
        "id": "help-1",
        "status": "pending",
        "peer_id": "ravn-specification-framer",
        "persona": "specification-framer",
        "summary": "Which namespaces are in scope for the PVC summary?",
        "reason": "needs_context",
        "recommendation": "Assume all namespaces unless told otherwise.",
        "attempted": ["re-read the build request"],
        "context": {},
    }

    def _blocked_with_question(
        self,
    ) -> tuple[TestClient, WorkflowCampaign, RecordingVolundrPort]:
        campaign = _make_campaign(status=WorkflowCampaignStatus.BLOCKED)
        port = RecordingVolundrPort()
        port.help_requests = [dict(self._QUESTION)]
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            volundr=port,
        )
        return client, campaign, port

    @staticmethod
    def _answer_params(task_id: str, *, text: str, metadata: dict | None = None) -> dict:
        return {
            "message": {
                "messageId": "msg-3",
                "role": "ROLE_USER",
                "taskId": task_id,
                "parts": [{"text": text}] if text else [],
                "metadata": metadata or {},
            }
        }

    def test_plain_reply_answers_pending_question(self) -> None:
        client, campaign, port = self._blocked_with_question()

        response = _rpc(
            client,
            "SendMessage",
            self._answer_params(campaign.slug, text="All namespaces, read-only access."),
        )

        result = response.json()["result"]["task"]
        assert result["status"]["state"] == "TASK_STATE_WORKING"
        assert port.answered_help == [
            ("session-123", "help-1", "All namespaces, read-only access.", "a2a")
        ]

    def test_reply_without_text_is_invalid(self) -> None:
        client, campaign, port = self._blocked_with_question()

        response = _rpc(client, "SendMessage", self._answer_params(campaign.slug, text=""))

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "non-empty text part" in error["message"]
        assert port.answered_help == []

    def test_request_id_metadata_selects_question(self) -> None:
        client, campaign, port = self._blocked_with_question()
        port.help_requests.append({**self._QUESTION, "id": "help-2", "summary": "Second question"})

        response = _rpc(
            client,
            "SendMessage",
            self._answer_params(
                campaign.slug,
                text="Answer for the second question.",
                metadata={"requestId": "help-2"},
            ),
        )

        assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
        assert port.answered_help[0][1] == "help-2"

    def test_answered_question_is_not_reanswerable(self) -> None:
        client, campaign, port = self._blocked_with_question()
        port.help_requests[0]["status"] = "answered"

        response = _rpc(
            client,
            "SendMessage",
            self._answer_params(campaign.slug, text="Too late."),
        )

        error = response.json()["error"]
        assert "no pending question" in error["message"]
        assert port.answered_help == []

    def test_get_task_attaches_pending_questions(self) -> None:
        client, campaign, _ = self._blocked_with_question()

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        task = response.json()["result"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        questions = task["metadata"]["pendingQuestions"]
        assert questions == [
            {
                "requestId": "help-1",
                "persona": "specification-framer",
                "question": "Which namespaces are in scope for the PVC summary?",
                "reason": "needs_context",
                "recommendation": "Assume all namespaces unless told otherwise.",
                "attempted": ["re-read the build request"],
            }
        ]
