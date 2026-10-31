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
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from ravn.adapters.tool_build.a2a import A2AToolBuildBackend
from ravn.adapters.tool_build.http import HttpxJsonClient
from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest
from tests.integration.helpers.sse import start_server
from ting.adapters.postgres_a2a_launches import PostgresA2ALaunchReservationRepository
from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.adapters.volundr_factory import LocalVolundrAdapterFactory
from ting.api.a2a import create_a2a_router, resolve_a2a_launch_repo
from ting.api.a2a_card import create_agent_card_router, resolve_card_workflow_repo
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig, AuthConfig, Settings
from ting.domain.models import WorkflowDefinition, WorkflowScope
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


def _headers() -> dict[str, str]:
    roles = base64.b64encode(json.dumps(["volundr:developer"]).encode()).decode()
    return {
        "A2A-Version": "1.0",
        "x-auth-user-id": "a2a-integration-user",
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
