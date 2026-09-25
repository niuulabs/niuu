"""Tests for the A2A task endpoint over workflow launches."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.models import Principal
from ting.api.a2a import (
    _launch_digest,
    campaign_to_task,
    create_a2a_router,
    resolve_a2a_launch_repo,
)
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import create_research_router, resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig, AuthConfig, Settings
from ting.domain.a2a_launch import A2ALaunchReservation
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

    async def list_workflow_versions(self, workflow_id):
        return []

    async def get_workflow_version(self, workflow_id, *, version=None, document_revision=None):
        workflow = await self.get_workflow(workflow_id)
        return workflow if workflow is not None and workflow.version == version else None

    async def save_workflow_version(self, workflow, **kwargs):
        raise NotImplementedError

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    async def has_recorded_version_history(self, workflow_id: UUID) -> bool:
        return True

    async def adopt_legacy_bundled(self, seed):
        return await self.save_workflow(seed)


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


class InMemoryA2ALaunchRepository:
    def __init__(self) -> None:
        self.by_message = {}

    async def reserve(self, reservation):
        key = (reservation.owner_id, reservation.tenant_id, reservation.message_id)
        existing = self.by_message.get(key)
        if existing is not None:
            if existing.request_digest != reservation.request_digest:
                raise ValueError("different content")
            return existing, False
        self.by_message[key] = reservation
        return reservation, True

    async def claim(self, reservation_id, *, lease_token, lease_until):
        for key, reservation in self.by_message.items():
            if reservation.id != reservation_id:
                continue
            if reservation.state == "launched":
                return None
            from dataclasses import replace

            claimed = replace(
                reservation,
                state="launching",
                lease_token=lease_token,
                lease_expires_at=lease_until,
            )
            self.by_message[key] = claimed
            return claimed
        return None

    async def mark_launched(self, reservation_id, *, lease_token, session_id):
        from dataclasses import replace

        for key, reservation in self.by_message.items():
            if reservation.id == reservation_id:
                launched = replace(
                    reservation,
                    state="launched",
                    session_id=session_id,
                    lease_token=None,
                )
                self.by_message[key] = launched
                return launched
        raise ValueError("missing reservation")


class RecordingVolundrPort(VolundrPort):
    def __init__(
        self,
        *,
        session_status: str = "starting",
        stop_failures: int = 0,
        name: str = "local",
        target_id: str = "local",
        tags: list[str] | None = None,
        sessions: list[VolundrSession] | None = None,
        spawn_failure: bool = False,
    ) -> None:
        self._session_status = session_status
        self._name = name
        self._target_id = target_id
        self._tags = tags or []
        self.sessions: list[VolundrSession] = list(sessions or [])
        self._spawn_failure = spawn_failure
        self.spawned: list[SpawnRequest] = []
        self.stopped: list[str] = []
        self.stop_attempts: list[str] = []
        self.stop_failures = stop_failures
        self.gates: list[dict] = []
        self.resolved_gates: list[tuple[str, str, str, str, str]] = []
        self.help_requests: list[dict] = []
        self.answered_help: list[tuple[str, str, str, str]] = []
        self.auth_calls: list[tuple[str, str | None, Principal | None]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def tags(self) -> list[str]:
        return self._tags

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        self.auth_calls.append(("spawn", auth_token, principal))
        self.spawned.append(request)
        if self._spawn_failure:
            raise RuntimeError("spawn failed")
        return VolundrSession(
            id="session-123",
            name=request.name,
            status=self._session_status,
            chat_endpoint="wss://sessions.example/s/session-123/session",
            tracker_issue_id=request.tracker_issue_id,
            cluster_name=self._name,
            repo=request.repo,
            branch=request.branch,
            base_branch=request.base_branch,
            workload_type=request.workload_type,
        )

    async def get_session(self, session_id: str, *, auth_token=None, principal=None):
        return None

    async def list_sessions(self, *, auth_token=None, principal=None):
        self.auth_calls.append(("list_sessions", auth_token, principal))
        return list(self.sessions)

    async def get_pr_status(self, session_id: str):
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        raise NotImplementedError

    async def send_message(self, session_id: str, message: str, *, auth_token=None, principal=None):
        raise NotImplementedError

    async def send_directed_room_message(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    async def get_workflow_gates(self, session_id: str, *, auth_token=None, principal=None):
        self.auth_calls.append(("get_workflow_gates", auth_token, principal))
        return list(self.gates)

    async def get_help_requests(self, session_id: str, *, auth_token=None, principal=None):
        self.auth_calls.append(("get_help_requests", auth_token, principal))
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
        self.auth_calls.append(("answer_help_request", auth_token, principal))
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
        self.auth_calls.append(("resolve_workflow_gate", auth_token, principal))
        self.resolved_gates.append((session_id, gate_id, decision, notes, source))
        return {"status": "resolved"}

    async def stop_session(self, session_id: str, *, auth_token=None, principal=None) -> None:
        self.auth_calls.append(("stop", auth_token, principal))
        self.stop_attempts.append(session_id)
        if self.stop_failures:
            self.stop_failures -= 1
            raise ConnectionError("runtime control temporarily unavailable")
        self.stopped.append(session_id)

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        self.auth_calls.append(("list_integration_ids", auth_token, principal))
        return ["integration-github", "integration-memory"]

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


class InMemoryPushRepository:
    def __init__(self) -> None:
        self.configs: dict[tuple[str, str, str], Any] = {}

    async def save_config(self, *, task_id: str, owner_id: str, config):
        saved = type(config)()
        saved.CopyFrom(config)
        saved.task_id = task_id
        saved.id = saved.id or "push-1"
        self.configs[(task_id, owner_id, saved.id)] = saved
        return saved

    async def get_for_owner(self, *, task_id: str, owner_id: str, config_id: str):
        return self.configs.get((task_id, owner_id, config_id))

    async def list_for_owner(self, *, task_id: str, owner_id: str):
        return [
            config
            for (stored_task, stored_owner, _), config in self.configs.items()
            if stored_task == task_id and stored_owner == owner_id
        ]

    async def delete_for_owner(self, *, task_id: str, owner_id: str, config_id: str):
        return self.configs.pop((task_id, owner_id, config_id), None) is not None


class RecordingPushDispatcher:
    enabled = True
    max_configs_page_size = 100

    def __init__(self) -> None:
        self.repo = InMemoryPushRepository()
        self.queued: list[str] = []

    def validate_config(self, config) -> None:
        if not config.url.startswith("https://resident.example/"):
            raise ValueError("callback origin is not allowed")
        if config.authentication.scheme and config.authentication.scheme.casefold() != "bearer":
            raise ValueError("unsupported authentication scheme")

    async def save_config(self, *, task_id: str, owner_id: str, config):
        return await self.repo.save_config(task_id=task_id, owner_id=owner_id, config=config)

    async def get_config(self, *, task_id: str, owner_id: str, config_id: str):
        return await self.repo.get_for_owner(
            task_id=task_id,
            owner_id=owner_id,
            config_id=config_id,
        )

    async def list_configs(self, *, task_id: str, owner_id: str):
        return await self.repo.list_for_owner(task_id=task_id, owner_id=owner_id)

    async def delete_config(self, *, task_id: str, owner_id: str, config_id: str):
        return await self.repo.delete_for_owner(
            task_id=task_id,
            owner_id=owner_id,
            config_id=config_id,
        )

    async def queue_campaign(self, campaign: WorkflowCampaign) -> int:
        self.queued.append(campaign.slug)
        return 1


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


def _make_workflow_with_placement(
    placement: dict, *, name: str = "tool-builder"
) -> WorkflowDefinition:
    workflow = _make_workflow(name=name)
    return replace(
        workflow,
        schema_version=2,
        graph={**workflow.graph, "placement": placement},
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


def _build_token(scopes: list[str], **claims: Any) -> str:
    return jwt.encode(
        {
            "sub": "user-1",
            "token_use": "valkyrie_build",
            "scopes": scopes,
            **claims,
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


def _make_client(
    *,
    workflow_repo: WorkflowRepository | None = None,
    campaign_repo: WorkflowCampaignRepository | None = None,
    volundr: RecordingVolundrPort | None = None,
    volundr_factory: RecordingVolundrFactory | None = None,
    settings: Settings | None = None,
    push_dispatcher: Any | None = None,
    launch_repo: InMemoryA2ALaunchRepository | None = None,
) -> tuple[TestClient, InMemoryCampaignRepository, RecordingVolundrPort]:
    workflow_repo = workflow_repo or InMemoryWorkflowRepository()
    campaigns = campaign_repo or InMemoryCampaignRepository()
    port = volundr or RecordingVolundrPort()
    app = FastAPI()
    app.state.authorization = AllowAllAuthorizationAdapter()
    app.include_router(create_a2a_router())
    app.include_router(create_research_router())
    app.state.settings = settings or Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.a2a_push_dispatcher = push_dispatcher
    launch_repo = launch_repo or InMemoryA2ALaunchRepository()
    app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaigns
    app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launch_repo
    app.dependency_overrides[resolve_volundr_factory] = lambda: (
        volundr_factory or RecordingVolundrFactory([port])
    )
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


def test_campaign_to_task_exposes_connection_id_when_set() -> None:
    campaign = _make_campaign(connection_id="spark-01")
    task = campaign_to_task(campaign)
    assert task.metadata["connectionId"] == "spark-01"


def test_campaign_to_task_omits_connection_id_when_unset() -> None:
    campaign = _make_campaign(connection_id=None)
    task = campaign_to_task(campaign)
    assert "connectionId" not in task.metadata


def _send_params(skill_id: str, *, prompt: str = "Build the widget tool") -> dict[str, Any]:
    return {
        "message": {
            "messageId": "msg-1",
            "role": "ROLE_USER",
            "parts": [{"text": prompt}],
            "metadata": {"skillId": skill_id, "model": "gpt-5.5"},
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
        assert result["metadata"]["skillId"] == str(workflow.id)
        assert result["metadata"]["sessionId"] == "session-123"

        assert len(port.spawned) == 1
        assert port.spawned[0].workload_type == "ravn_flock"
        assert port.spawned[0].integration_ids == ["integration-github", "integration-memory"]
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

    def test_reused_message_id_rejects_changed_launch_content(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )

        first = _rpc(client, "SendMessage", _send_params(str(workflow.id)))
        changed = _rpc(
            client,
            "SendMessage",
            _send_params(str(workflow.id), prompt="Build a different widget"),
        )

        assert "result" in first.json()
        assert changed.json()["error"]["code"] == -32602
        assert "reused for different launch content" in changed.json()["error"]["message"]
        assert len(port.spawned) == 1

    def test_workflow_id_does_not_select_an_a2a_skill(self) -> None:
        client, _, _ = _make_client()
        params = _send_params(str(uuid4()))
        skill_id = params["message"]["metadata"].pop("skillId")
        params["message"]["metadata"]["workflowId"] = skill_id

        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 200
        error = response.json()["error"]
        assert error["code"] == -32602
        assert "skillId" in error["message"]

    def test_unknown_skill_is_invalid_params(self) -> None:
        client, _, _ = _make_client()

        response = _rpc(client, "SendMessage", _send_params(str(uuid4())))

        error = response.json()["error"]
        assert error["code"] == -32602
        assert "unknown skill" in error["message"]

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
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
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
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
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
        assert {name for name, _, _ in port.auth_calls} >= {
            "list_sessions",
            "list_integration_ids",
            "spawn",
        }
        assert all(token is None for _, token, _ in port.auth_calls)
        principals = [principal for _, _, principal in port.auth_calls]
        assert all(principal and principal.user_id == "user-1" for principal in principals)

    def test_production_identity_rejects_execution_gateway_launch_without_lineage(self) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
        attempt_id = uuid4()
        token = _build_token(
            ["ting:workflow:launch"],
            workload_sub=f"workflow-child:{attempt_id}",
            workload_workflow_execution_id=str(uuid4()),
            workload_child_attempt_id=str(attempt_id),
            workload_child_intent_id=str(uuid4()),
        )

        response = _rpc(
            client,
            "SendMessage",
            _send_params(str(workflow.id)),
            headers=_headers(token=token),
        )

        assert response.status_code == 403
        assert port.spawned == []

    def test_workflow_child_inherits_pinned_result_schema_into_runtime(self, monkeypatch) -> None:
        workflow = _make_workflow()
        client, _, port = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            settings=Settings(auth=AuthConfig(allow_anonymous_dev=True)),
        )
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
        execution_id = uuid4()
        attempt_id = uuid4()
        intent_id = uuid4()
        result_schema = {
            "type": "object",
            "properties": {"evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["evidence"],
            "additionalProperties": False,
        }
        execution = SimpleNamespace(
            id=execution_id,
            owner_id="user-1",
            tenant_id="",
            parent_node_id="delivery-workstreams",
            policy=SimpleNamespace(
                coordinator_id="developer-coordinator",
                result_schema=result_schema,
            ),
        )
        child = SimpleNamespace(
            id=attempt_id,
            execution_id=execution_id,
            intent_id=intent_id,
            message_id="msg-1",
            template_id=workflow.id,
        )

        class Ledger:
            async def get_child(self, child_id: UUID):
                return child if child_id == attempt_id else None

            async def get_internal(self, requested_id: UUID):
                return execution if requested_id == execution_id else None

            async def get(self, requested_id: UUID, *, owner_id: str, tenant_id: str):
                if (
                    requested_id == execution_id
                    and owner_id == execution.owner_id
                    and tenant_id == execution.tenant_id
                ):
                    return execution
                return None

        client.app.state.workflow_execution_repo = Ledger()
        monkeypatch.setattr("ting.api.a2a.pinned_child_workflow", lambda *_args: workflow)
        token = _build_token(
            ["ting:workflow:launch"],
            workload_sub=f"workflow-child:{attempt_id}",
            workload_workflow_execution_id=str(execution_id),
            workload_child_attempt_id=str(attempt_id),
            workload_child_intent_id=str(intent_id),
        )
        params = _send_params(str(workflow.id))
        params["message"]["metadata"].update(
            {
                "workflowExecution": {"executionId": str(execution_id)},
                "attemptId": str(attempt_id),
                "intentId": str(intent_id),
                "messageId": "msg-1",
            }
        )

        response = _rpc(client, "SendMessage", params, headers=_headers(token=token))

        assert response.status_code == 200, response.text
        assert port.spawned[0].workload_config["workflow_result_schema"] == result_schema
        developer_runtime = port.spawned[0].workload_config["ravn_config"]["workflow_execution"]
        assert "result_schema" not in developer_runtime

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
        assert port.spawned == []
        assert port.stopped == []

    def test_launch_resolves_pinned_placement_tags_over_multiple_targets(self) -> None:
        workflow = _make_workflow_with_placement({"tags": ["dgx-spark"]})
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        spark = RecordingVolundrPort(name="spark-01", target_id="spark-01", tags=["dgx-spark"])
        client, campaigns, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([cpu, spark]),
        )

        response = _rpc(client, "SendMessage", _send_params(str(workflow.id)))

        assert response.status_code == 200, response.text
        assert cpu.spawned == []
        assert len(spark.spawned) == 1
        task = response.json()["result"]["task"]
        assert task["metadata"]["connectionId"] == "spark-01"

        import asyncio

        campaign = asyncio.run(campaigns.get_campaign_by_slug(task["id"]))
        assert campaign is not None
        assert campaign.connection_id == "spark-01"

    def test_launch_rejects_pinned_placement_with_no_visible_match(self) -> None:
        workflow = _make_workflow_with_placement({"instance": "spark-01"})
        cpu_only = RecordingVolundrPort(name="cpu-only", target_id="cpu-only")
        client, campaigns, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([cpu_only]),
        )

        response = _rpc(client, "SendMessage", _send_params(str(workflow.id)))

        assert response.status_code == 422
        assert "spark-01" in response.text
        assert cpu_only.spawned == []

        # The rejected launch must not leave a campaign stuck PENDING
        # forever — either no campaign was ever created (resolution now
        # happens before the campaign is saved) or it was resolved to a
        # terminal state. Either way, no retry of this message should ever
        # find a PENDING row it can only fail against again.
        import asyncio

        campaigns_list = asyncio.run(campaigns.list_campaigns(owner_id="user-1"))
        assert all(c.status != WorkflowCampaignStatus.PENDING for c in campaigns_list)

    def test_launch_rejects_metadata_connection_id_conflicting_with_placement(self) -> None:
        workflow = _make_workflow_with_placement({"tags": ["dgx-spark"]})
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        spark = RecordingVolundrPort(name="spark-01", target_id="spark-01", tags=["dgx-spark"])
        client, campaigns, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([cpu, spark]),
        )
        params = _send_params(str(workflow.id))
        params["message"]["metadata"]["connectionId"] = "cpu"

        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 422
        assert "cpu" in response.text
        assert cpu.spawned == []
        assert spark.spawned == []

        import asyncio

        campaigns_list = asyncio.run(campaigns.list_campaigns(owner_id="user-1"))
        assert all(c.status != WorkflowCampaignStatus.PENDING for c in campaigns_list)

    def test_retry_reuses_the_persisted_target_and_does_not_duplicate_spawn(self) -> None:
        """A retry (e.g. after a crash between spawn and mark_launched) must
        land on the exact target the first attempt resolved and already
        spawned on — never re-resolve and possibly pick a different Guild
        target, which would duplicate the session."""
        import asyncio

        workflow = _make_workflow_with_placement({"tags": ["dgx-spark"]})
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        spark = RecordingVolundrPort(name="spark-01", target_id="spark-01", tags=["dgx-spark"])
        launch_repo = InMemoryA2ALaunchRepository()
        client, campaigns, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([cpu, spark]),
            launch_repo=launch_repo,
        )

        prompt = "Build the widget tool"
        metadata = {"skillId": str(workflow.id), "model": "gpt-5.5"}
        reservation_id = uuid4()
        campaign_id = uuid4()
        task_id = f"a2a-{reservation_id.hex}"
        now = datetime.now(UTC)

        # Seed the state a first attempt would have left behind had it
        # crashed after resolving+persisting its target and spawning a
        # session, but before it could call mark_launched: a "launching"
        # reservation, a PENDING campaign already carrying the resolved
        # (non-primary) spark connection, and a session on spark whose
        # tracker key matches this reservation.
        asyncio.run(
            launch_repo.reserve(
                A2ALaunchReservation(
                    id=reservation_id,
                    owner_id="user-1",
                    tenant_id="",
                    message_id="msg-1",
                    request_digest=_launch_digest(prompt, metadata),
                    workflow_id=workflow.id,
                    task_id=task_id,
                    campaign_id=campaign_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        )
        asyncio.run(
            launch_repo.claim(
                reservation_id,
                lease_token=uuid4(),
                lease_until=now + timedelta(seconds=60),
            )
        )
        pending_snapshot = build_workflow_snapshot(workflow)
        asyncio.run(
            campaigns.save_campaign(
                WorkflowCampaign(
                    id=campaign_id,
                    slug=task_id,
                    name=workflow.name,
                    owner_id="user-1",
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    workflow_name=workflow.name,
                    workflow_snapshot=pending_snapshot,
                    session_id="",
                    session_name="",
                    status=WorkflowCampaignStatus.PENDING,
                    active_stage_id=None,
                    stage_state=[],
                    metadata={
                        "surface": "a2a",
                        "prompt": prompt,
                        "a2a_context_id": task_id,
                        "a2a_message_id": "msg-1",
                        "a2a_workflow_slug": "build-the-widget-tool",
                    },
                    created_at=now,
                    updated_at=now,
                    last_activity_at=now,
                    connection_id="spark-01",
                )
            )
        )
        spark.sessions.append(
            VolundrSession(
                id="recovered-session",
                name="recovered",
                status="running",
                chat_endpoint="wss://sessions.example/s/recovered-session/session",
                tracker_issue_id=f"workflow:{task_id}",
                cluster_name="spark-01",
                repo="",
                branch="",
                base_branch="",
                workload_type="ravn_flock",
            )
        )

        params = _send_params(str(workflow.id), prompt=prompt)
        params["message"]["messageId"] = "msg-1"
        response = _rpc(client, "SendMessage", params)

        assert response.status_code == 200, response.text
        assert cpu.spawned == []
        assert spark.spawned == []  # recovered, not re-spawned
        task = response.json()["result"]["task"]
        assert task["id"] == task_id
        assert task["metadata"]["sessionId"] == "recovered-session"
        assert task["metadata"]["connectionId"] == "spark-01"

        campaign = asyncio.run(campaigns.get_campaign_by_slug(task_id))
        assert campaign is not None
        assert campaign.connection_id == "spark-01"
        assert campaign.session_id == "recovered-session"

    def test_launch_failure_marks_the_campaign_failed_instead_of_leaving_it_pending(
        self,
    ) -> None:
        """A failure inside the launch attempt itself (after the campaign
        row is already saved) must resolve that row, not leave it PENDING —
        the placement pre-check only prevents the case where resolution
        itself fails before the campaign is ever created."""
        import asyncio

        workflow = _make_workflow_with_placement({"tags": ["dgx-spark"]})
        spark = RecordingVolundrPort(
            name="spark-01", target_id="spark-01", tags=["dgx-spark"], spawn_failure=True
        )
        client, campaigns, _ = _make_client(
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([spark]),
        )

        response = _rpc(client, "SendMessage", _send_params(str(workflow.id)))

        assert "error" in response.json()
        campaigns_list = asyncio.run(campaigns.list_campaigns(owner_id="user-1"))
        assert len(campaigns_list) == 1
        [campaign] = campaigns_list
        assert campaign.status == WorkflowCampaignStatus.FAILED
        assert "spawn failed" in campaign.metadata["failure_error"]


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
    def test_scoped_launcher_cannot_read_owner_tasks_without_ledger_claims(self) -> None:
        campaign = _make_campaign()
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
        )
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
        token = _build_token(["ting:workflow:launch"])

        response = _rpc(
            client,
            "GetTask",
            {"id": campaign.slug},
            headers=_headers(token=token),
        )

        assert response.status_code == 403

    def test_execution_gateway_token_is_bound_to_exact_ledger_task(self) -> None:
        execution_id = uuid4()
        attempt_id = uuid4()
        intent_id = uuid4()
        campaign = _make_campaign(slug="task-authorized")
        other = _make_campaign(slug="task-same-owner")
        execution = SimpleNamespace(
            id=execution_id,
            owner_id="user-1",
            tenant_id="",
        )
        child = SimpleNamespace(
            id=attempt_id,
            execution_id=execution_id,
            intent_id=intent_id,
            task_id=campaign.slug,
        )

        class Ledger:
            async def get_child(self, child_id: UUID):
                return child if child_id == attempt_id else None

            async def get(self, requested_id: UUID, *, owner_id: str, tenant_id: str):
                if (
                    requested_id == execution_id
                    and owner_id == execution.owner_id
                    and tenant_id == execution.tenant_id
                ):
                    return execution
                return None

        client, _, port = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign, other]),
        )
        client.app.state.identity = EnvoyHeaderAuthenticationAdapter()
        client.app.state.workflow_execution_repo = Ledger()
        token = _build_token(
            ["ting:workflow:launch"],
            workload_sub=f"workflow-child:{attempt_id}",
            workload_workflow_execution_id=str(execution_id),
            workload_child_attempt_id=str(attempt_id),
            workload_child_intent_id=str(intent_id),
            workload_child_task_id=campaign.slug,
        )
        headers = _headers(token=token)

        authorized = _rpc(client, "GetTask", {"id": campaign.slug}, headers=headers)
        denied = _rpc(client, "GetTask", {"id": other.slug}, headers=headers)
        canceled = _rpc(client, "CancelTask", {"id": campaign.slug}, headers=headers)

        assert authorized.status_code == 200
        assert authorized.json()["result"]["id"] == campaign.slug
        assert denied.status_code == 403
        assert canceled.status_code == 200
        assert port.auth_calls[-1][0] == "stop"
        assert port.auth_calls[-1][1] is None
        assert port.auth_calls[-1][2].user_id == "user-1"

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

    def test_failed_task_exposes_worker_error(self) -> None:
        campaign = _make_campaign(status=WorkflowCampaignStatus.FAILED)
        campaign = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "metadata": {
                    **campaign.metadata,
                    "failure_error": "refresh token was already used",
                },
            }
        )
        client, _, _ = _make_client(campaign_repo=InMemoryCampaignRepository([campaign]))

        response = _rpc(client, "GetTask", {"id": campaign.slug})

        result = response.json()["result"]
        assert result["status"]["state"] == "TASK_STATE_FAILED"
        assert result["metadata"]["error"] == "refresh token was already used"

    def test_completed_task_projects_only_server_stored_delivery_result(self) -> None:
        campaign = _make_campaign(
            status=WorkflowCampaignStatus.COMPLETED,
            workflow_snapshot={
                "graph": {"nodes": [], "edges": []},
                "workflow_revision": "content-rev",
                "workflow_digest": "sha256:" + "a" * 64,
            },
            metadata={
                "delivery": {
                    "schemaVersion": 1,
                    "result": {"attemptId": "attempt-1", "candidateSha": "b" * 40},
                    "reviews": [{"eventId": "review-1", "valid": True}],
                },
            },
        )
        client, _, _ = _make_client(campaign_repo=InMemoryCampaignRepository([campaign]))

        result = _rpc(client, "GetTask", {"id": campaign.slug}).json()["result"]
        delivery = result["metadata"]["deliveryResult"]
        assert delivery["attemptId"] == "attempt-1"
        assert delivery["_trustedReviewEnvelope"] == {
            "schemaVersion": 1,
            "taskId": campaign.slug,
            "sessionId": campaign.session_id,
            "workflowId": str(campaign.workflow_id),
            "workflowRevision": "content-rev",
            "workflowDigest": "sha256:" + "a" * 64,
            "reviews": [{"eventId": "review-1", "valid": True}],
        }

    def test_terminal_result_waits_for_cleanup_and_retries_transient_failure(self) -> None:
        campaign = _make_campaign(
            status=WorkflowCampaignStatus.COMPLETED,
            metadata={
                "delivery": {
                    "schemaVersion": 1,
                    "result": {"attemptId": "attempt-1"},
                    "reviews": [{"eventId": "review-1", "valid": True}],
                }
            },
        )
        port = RecordingVolundrPort(stop_failures=1)
        client, campaigns, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            volundr=port,
        )

        pending = _rpc(client, "GetTask", {"id": campaign.slug})
        assert pending.status_code == 503
        assert port.stop_attempts == [campaign.session_id]

        completed = _rpc(client, "GetTask", {"id": campaign.slug})
        assert completed.status_code == 200
        result = completed.json()["result"]
        assert result["status"]["state"] == "TASK_STATE_COMPLETED"
        assert result["metadata"]["deliveryResult"]["attemptId"] == "attempt-1"

        repeated = _rpc(client, "GetTask", {"id": campaign.slug})
        assert repeated.status_code == 200
        assert port.stop_attempts == [campaign.session_id, campaign.session_id]
        saved = next(iter(campaigns._campaigns.values()))
        assert saved.status == WorkflowCampaignStatus.COMPLETED
        assert saved.metadata["delivery"] == campaign.metadata["delivery"]
        assert saved.metadata["terminal_session_stopped"] is True

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


class TestPushNotifications:
    def test_create_get_list_and_delete_owned_callback(self) -> None:
        campaign = _make_campaign()
        dispatcher = RecordingPushDispatcher()
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            push_dispatcher=dispatcher,
        )

        created = _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {
                "taskId": campaign.slug,
                "id": "ivaldi-callback",
                "url": "https://resident.example/a2a/push",
                "authentication": {"scheme": "Bearer"},
            },
        ).json()["result"]

        assert created["taskId"] == campaign.slug
        assert created["id"] == "ivaldi-callback"
        assert dispatcher.queued == [campaign.slug]
        fetched = _rpc(
            client,
            "GetTaskPushNotificationConfig",
            {"taskId": campaign.slug, "id": "ivaldi-callback"},
        ).json()["result"]
        assert fetched["url"] == "https://resident.example/a2a/push"
        listed = _rpc(
            client,
            "ListTaskPushNotificationConfigs",
            {"taskId": campaign.slug},
        ).json()["result"]
        assert [config["id"] for config in listed["configs"]] == ["ivaldi-callback"]
        deleted = _rpc(
            client,
            "DeleteTaskPushNotificationConfig",
            {"taskId": campaign.slug, "id": "ivaldi-callback"},
        )
        assert deleted.status_code == 200
        assert dispatcher.repo.configs == {}

    def test_rejects_unowned_or_unallowlisted_callback(self) -> None:
        campaign = _make_campaign()
        dispatcher = RecordingPushDispatcher()
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
            push_dispatcher=dispatcher,
        )

        unowned = _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {
                "taskId": campaign.slug,
                "url": "https://resident.example/a2a/push",
            },
            headers=_headers(user_id="user-2"),
        ).json()["error"]
        assert unowned["code"] == -32001
        unsafe = _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {
                "taskId": campaign.slug,
                "url": "https://attacker.example/a2a/push",
            },
        ).json()["error"]
        assert unsafe["code"] == -32602
        assert dispatcher.repo.configs == {}

    def test_push_methods_are_unsupported_when_outbox_is_disabled(self) -> None:
        campaign = _make_campaign()
        client, _, _ = _make_client(
            campaign_repo=InMemoryCampaignRepository([campaign]),
        )

        error = _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {"taskId": campaign.slug, "url": "https://resident.example/a2a/push"},
        ).json()["error"]

        assert error["code"] == -32004


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
