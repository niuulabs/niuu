"""Production-adapter A2A workflow task lifecycle integration coverage.

This test deliberately crosses Ting's JSON-RPC adapter, its PostgreSQL
workflow/campaign/reservation repositories, and VolundrHTTPAdapter over a
real loopback HTTP connection.  The Volundr fixture persists the launched
session with its production session repository, so CancelTask verifies the
same session through both services rather than through a recording port.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.tool_build.a2a import A2AToolBuildBackend
from ravn.adapters.tool_build.http import HttpxJsonClient
from ravn.adapters.tools.mimir_tools import MimirWriteTool
from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest
from skuld.broker import Broker
from skuld.config import SkuldSettings
from tests.integration.helpers.sse import start_server
from tests.test_ravn.conftest import _make_agent_task, _make_drive_loop
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.adapters.postgres_a2a_launches import PostgresA2ALaunchReservationRepository
from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.adapters.volundr_factory import LocalVolundrAdapterFactory
from ting.api import research as research_api
from ting.api.a2a import create_a2a_router, resolve_a2a_launch_repo
from ting.api.a2a_card import create_agent_card_router, resolve_card_workflow_repo
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig, AuthConfig, Settings
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.ports.volundr import ActivityEvent
from ting.system_workflows import load_system_workflows
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.domain.models import SessionStatus

_A2A_PATH = "/api/v1/ting/a2a"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


def _workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="a2a-lifecycle",
        description="Launches an isolated A2A integration task.",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["a2a", "integration"],
            "nodes": [
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Lifecycle",
                    "stageMembers": [{"personaId": "tool-smith", "model": "gpt-5.5"}],
                }
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
    )


def _headers(*, user_id: str = "a2a-integration-user") -> dict[str, str]:
    roles = base64.b64encode(json.dumps(["volundr:developer"]).encode()).decode()
    return {
        "A2A-Version": "1.0",
        "x-auth-user-id": user_id,
        "x-auth-email": "a2a-integration@example.test",
        "x-auth-tenant": "default",
        "x-auth-roles": roles,
    }


def _request(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": method, "method": method, "params": params}


async def _stop_server(server) -> None:
    server.should_exit = True


async def test_a2a_task_lifecycle_uses_postgres_and_volundr_http(
    txn_pool,
    volundr_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One persisted A2A task supports SendMessage, GetTask, and CancelTask."""

    # Ting's production launch code asks its target for enabled integrations
    # before it creates the session.  The session fixture intentionally mounts
    # only Forge's session router, so provide the empty production response
    # shape while leaving session create/get/stop on Volundr's real REST router.
    @volundr_app.get("/api/v1/integrations")
    async def integrations() -> list[dict[str, str]]:
        return []

    volundr_server, volundr_url = await start_server(volundr_app)
    workflow_repo = PostgresWorkflowRepository(txn_pool)
    campaign_repo = PostgresWorkflowCampaignRepository(txn_pool)
    launch_repo = PostgresA2ALaunchReservationRepository(txn_pool)
    workflow = await workflow_repo.save_workflow(_workflow())

    ting_app = FastAPI()
    ting_app.state.authorization = AllowAllAuthorizationAdapter()
    ting_app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    ting_app.include_router(create_a2a_router())
    ting_app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    ting_app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launch_repo
    ting_app.dependency_overrides[resolve_volundr_factory] = lambda: LocalVolundrAdapterFactory(
        volundr_url
    )
    caplog.set_level(logging.INFO, logger="ting.api.a2a")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=ting_app),
            base_url="http://ting.integration.test",
        ) as client:
            sent = await client.post(
                _A2A_PATH,
                headers=_headers(),
                json=_request(
                    "SendMessage",
                    {
                        "message": {
                            "messageId": f"a2a-integration-{uuid4()}",
                            "role": "ROLE_USER",
                            "parts": [{"text": "Launch then cancel this A2A integration task."}],
                            "metadata": {"skillId": str(workflow.id), "model": "gpt-5.5"},
                        }
                    },
                ),
            )
            assert sent.status_code == 200, sent.text
            task_id = sent.json()["result"]["task"]["id"]
            session_id = sent.json()["result"]["task"]["metadata"]["sessionId"]

            fetched = await client.post(
                _A2A_PATH,
                headers=_headers(),
                json=_request("GetTask", {"id": task_id}),
            )
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["result"]["id"] == task_id
            assert fetched.json()["result"]["metadata"]["sessionId"] == session_id

            canceled = await client.post(
                _A2A_PATH,
                headers=_headers(),
                json=_request("CancelTask", {"id": task_id}),
            )
            assert canceled.status_code == 200, canceled.text
            assert canceled.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"

        campaign = await campaign_repo.get_campaign_by_slug(
            task_id,
            owner_id="a2a-integration-user",
        )
        assert campaign is not None
        assert campaign.metadata["a2a_canceled"] is True
        assert campaign.session_id == session_id

        lifecycle_logs = [
            record.getMessage()
            for record in caplog.records
            if "A2A JSON-RPC" in record.getMessage()
        ]
        assert lifecycle_logs == [
            (
                "A2A JSON-RPC SendMessage finished outcome=ok "
                f"task_id={task_id} workflow_id={workflow.id}"
            ),
            (
                "A2A JSON-RPC GetTask finished outcome=ok "
                f"task_id={task_id} workflow_id={workflow.id}"
            ),
            (
                "A2A JSON-RPC CancelTask finished outcome=ok "
                f"task_id={task_id} workflow_id={workflow.id}"
            ),
        ]

        session = await PostgresSessionRepository(txn_pool).get(UUID(session_id))
        assert session is not None
        assert session.status is SessionStatus.STOPPED
    finally:
        await _stop_server(volundr_server)


async def test_developer_planning_artifact_projection_survives_restart(
    txn_pool,
    volundr_app: FastAPI,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise published and failed planning paths across Ravn, Skuld, and Ting.

    The A2A launch and campaign reload use production PostgreSQL adapters. Ravn
    records the successful Mímir tool result, Skuld derives the terminal
    outcome from the bundled planning graph, and Ting projects it before a
    rebuilt A2A app reads the durable campaign.
    """

    @volundr_app.get("/api/v1/integrations")
    async def integrations() -> list[dict[str, str]]:
        return []

    volundr_server, volundr_url = await start_server(volundr_app)
    workflow_repo = PostgresWorkflowRepository(txn_pool)
    campaign_repo = PostgresWorkflowCampaignRepository(txn_pool)
    launch_repo = PostgresA2ALaunchReservationRepository(txn_pool)
    workflow = next(item for item in load_system_workflows() if item.name == "Developer Planning")
    workflow = await workflow_repo.save_workflow(workflow)
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    monkeypatch.setattr(research_api, "_campaign_knowledge", lambda *_args, **_kwargs: mimir)

    def build_app(
        workflows: PostgresWorkflowRepository,
        campaigns: PostgresWorkflowCampaignRepository,
        launches: PostgresA2ALaunchReservationRepository,
    ) -> FastAPI:
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
        app.include_router(create_a2a_router())
        app.dependency_overrides[resolve_workflow_repo] = lambda: workflows
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaigns
        app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launches
        app.dependency_overrides[resolve_volundr_factory] = lambda: LocalVolundrAdapterFactory(
            volundr_url
        )
        return app

    async def launch(client: AsyncClient, message_id: str) -> str:
        response = await client.post(
            _A2A_PATH,
            headers=_headers(),
            json=_request(
                "SendMessage",
                {
                    "message": {
                        "messageId": message_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": "Plan durable A2A artifact projection."}],
                        "metadata": {"skillId": str(workflow.id), "model": "gpt-5.6-terra"},
                    }
                },
            ),
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]["task"]["id"]

    async def project_terminal(task_id: str, *, publish: bool) -> None:
        campaign = await campaign_repo.get_campaign_by_slug(
            task_id, owner_id="a2a-integration-user"
        )
        assert campaign is not None
        drive_loop = _make_drive_loop(journal_path=str(tmp_path / f"{task_id}.json"))
        drive_loop._mesh = AsyncMock()
        drive_loop._skuld_channel = None
        drive_loop._source_id = "integration-ravn"
        drive_loop._persona_config = SimpleNamespace(
            name="developer-analyst",
            produces=SimpleNamespace(
                event_type="developer.plan.revised",
                event_type_map={"approved": "developer.plan.approved"},
                schema={},
            ),
        )
        drive_loop.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"developer.plan.approved"}
        )
        ravn_task = _make_agent_task(task_id=f"ravn-{task_id}")
        ravn_task.session_id = campaign.session_id
        ravn_task.workflow_node_id = "planning-analysis"
        if publish:
            artifact_path = f"planning/{task_id}/approved-plan.md"
            plan = """# Approved plan

Revision: plan-7

Scope: durable projection.

Acceptance: retrieve after restart.

Reviews: architecture pass; correctness pass; security pass.
"""
            result = await MimirWriteTool(mimir).execute(
                {"path": artifact_path, "content": plan}
            )
            assert result.is_error is False, result.content
            drive_loop.record_tool_outcome_fields(
                task=ravn_task,
                event_type="mimir.page.written",
                fields={"page_path": artifact_path},
            )
        await drive_loop._emit_mesh_outcome_event(
            ravn_task,
            """---outcome---
verdict: approved
plan_revision: plan-7
base_sha: deadbeef
plan: Persist the approved plan.
---end---""",
            success=True,
        )
        _canonical, approved = [call.args[0] for call in drive_loop._mesh.publish.await_args_list]

        broker = Broker(
            SkuldSettings.model_validate(
                {
                    "session": {
                        "id": campaign.session_id,
                        "workspace_dir": str(tmp_path),
                        "initial_prompt": "Plan durable A2A artifact projection.",
                    },
                    "room": {"enabled": True},
                    "workflow": {"graph": campaign.workflow_snapshot["graph"]},
                    "workflow_trigger": {"enabled": True, "event_type": "delivery.requested"},
                }
            )
        )
        broker._emit_pipeline_event = AsyncMock()
        broker._finish_trace_span = AsyncMock()
        broker._report_activity_state = AsyncMock(return_value=True)
        broker._room_bridge = SimpleNamespace(
            participants={},
            register_mesh_peer=AsyncMock(),
            handle_collaboration_frame=AsyncMock(),
        )
        await broker._maybe_emit_workflow_terminal_outcome(
            "ravn-analyst",
            {"root_correlation_id": task_id, "data": {"workflow_activation_id": task_id}},
            approved.payload,
        )
        terminal = next(
            call.args[1]
            for call in broker._emit_pipeline_event.await_args_list
            if call.args[0] == "outcome"
            and call.args[1]["event_type"] == "developer.plan.approved"
        )
        await broker._maybe_report_flock_completion(
            "workflow-stop:planning-complete",
            {
                "metadata": {"event_type": "developer.plan.approved"},
                "data": {"fields": terminal["fields"], "valid": True},
            },
        )
        activity_metadata = broker._report_activity_state.await_args.kwargs["extra_metadata"]
        projector = WorkflowCampaignProjector(
            repo=campaign_repo,
            volundr_factory=LocalVolundrAdapterFactory(volundr_url),
            event_bus=InMemoryEventBus(),
        )
        assert await projector.handle_activity(
            ActivityEvent(campaign.session_id, "idle", activity_metadata, campaign.owner_id),
            campaign.owner_id,
        )

    try:
        app = build_app(workflow_repo, campaign_repo, launch_repo)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://ting.test"
        ) as client:
            completed_task = await launch(client, f"planning-complete-{uuid4()}")
        await project_terminal(completed_task, publish=True)

        # Recreate repositories and the FastAPI application: no in-memory campaign
        # state or handler cache is used for either authorized or denied reads.
        restarted_app = build_app(
            PostgresWorkflowRepository(txn_pool),
            PostgresWorkflowCampaignRepository(txn_pool),
            PostgresA2ALaunchReservationRepository(txn_pool),
        )
        async with AsyncClient(
            transport=ASGITransport(app=restarted_app), base_url="http://ting.test"
        ) as client:
            fetched = await client.post(
                _A2A_PATH, headers=_headers(), json=_request("GetTask", {"id": completed_task})
            )
            assert fetched.status_code == 200, fetched.text
            task = fetched.json()["result"]
            assert task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert task["artifacts"][0]["artifactId"] == (
                f"planning/{completed_task}/approved-plan.md"
            )
            assert "Revision: plan-7" in task["artifacts"][0]["parts"][0]["text"]
            repeated = await client.post(
                _A2A_PATH, headers=_headers(), json=_request("GetTask", {"id": completed_task})
            )
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["result"]["artifacts"] == task["artifacts"]
            denied = await client.post(
                _A2A_PATH,
                headers=_headers(user_id="unauthorized-user"),
                json=_request("GetTask", {"id": completed_task}),
            )
            assert "error" in denied.json()

            failed_task = await launch(client, f"planning-failed-{uuid4()}")
        await project_terminal(failed_task, publish=False)

        async with AsyncClient(
            transport=ASGITransport(app=restarted_app), base_url="http://ting.test"
        ) as client:
            failed = await client.post(
                _A2A_PATH, headers=_headers(), json=_request("GetTask", {"id": failed_task})
            )
            assert failed.json()["result"]["status"]["state"] == "TASK_STATE_FAILED"
            assert failed.json()["result"].get("artifacts", []) == []
    finally:
        await _stop_server(volundr_server)


async def test_ravn_a2a_tool_build_backend_commissions_real_workflow_task(
    txn_pool,
    volundr_app: FastAPI,
) -> None:
    """The concrete Ravn client launches a persisted Ting A2A build task.

    The backend crosses real loopback HTTP to Ting's card and JSON-RPC
    endpoints.  Ting then persists the campaign and launches its session over
    its normal HTTP adapter to Volundr.  The workflow deliberately stays
    active, so the backend's bounded polling fails loudly rather than
    inventing a completed artifact.
    """

    @volundr_app.get("/api/v1/integrations")
    async def integrations() -> list[dict[str, str]]:
        return []

    volundr_server, volundr_url = await start_server(volundr_app)
    workflow_repo = PostgresWorkflowRepository(txn_pool)
    campaign_repo = PostgresWorkflowCampaignRepository(txn_pool)
    launch_repo = PostgresA2ALaunchReservationRepository(txn_pool)
    workflow = await workflow_repo.save_workflow(_workflow())

    ting_app = FastAPI()
    ting_app.state.authorization = AllowAllAuthorizationAdapter()
    ting_app.state.settings = Settings(
        auth=AuthConfig(
            allow_anonymous_dev=True,
            default_user_id="a2a-tool-build-client",
            # Align with the Forge identity: sessions.tenant_id is a foreign key.
            default_tenant_id="default",
        ),
        a2a=A2AConfig(),
    )
    ting_app.include_router(create_a2a_router())
    ting_app.include_router(create_agent_card_router(ting_app.state.settings.a2a))
    ting_app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_card_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    ting_app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launch_repo
    ting_app.dependency_overrides[resolve_volundr_factory] = lambda: LocalVolundrAdapterFactory(
        volundr_url
    )
    ting_server, ting_url = await start_server(ting_app)

    backend = A2AToolBuildBackend(
        card_url=f"{ting_url}/.well-known/agent-card.json",
        workflow_id=str(workflow.id),
        client=HttpxJsonClient(allowed_origins=[ting_url]),
        max_poll_attempts=1,
        poll_interval_seconds=0,
    )
    request = ToolBuildRequest(
        name="integration_tool",
        description="Exercise the A2A tool-build client.",
        build_request="Build an integration tool.",
        input_schema={"type": "object"},
        required_permission="mimir:read",
        operation_id="a2a-tool-build-integration",
    )

    try:
        with pytest.raises(ToolBuildError, match="did not finish within 1 polls"):
            await backend.build(request)

        campaigns = await campaign_repo.list_campaigns(owner_id="a2a-tool-build-client")
        assert len(campaigns) == 1
        campaign = campaigns[0]
        assert campaign.workflow_id == workflow.id
        assert campaign.metadata["surface"] == "a2a"
        assert campaign.metadata["prompt"]
        session = await PostgresSessionRepository(txn_pool).get(UUID(campaign.session_id))
        assert session is not None
        assert session.status is not SessionStatus.STOPPED
    finally:
        await _stop_server(ting_server)
        await _stop_server(volundr_server)
