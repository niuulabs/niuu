"""Production-adapter A2A workflow task lifecycle integration coverage.

This test deliberately crosses Ting's JSON-RPC adapter, its PostgreSQL
workflow/campaign/reservation repositories, the PostgreSQL step ledger, the step
engine and Ting's Forge session adapter. Forge itself is a recording fake behind
the connection factory: it takes the step-host session and the step activation
(the contract Forge and Skuld implement) and records what it was asked, so the
test proves a real launch, GetTask and CancelTask without a Skuld to close steps.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.adapters.outbound.http_json_client import HttpxJsonClient
from personas.domain.persona import PersonaConfig, PersonaProduces
from personas.domain.persona_document import portable_persona_from_config
from ravn.adapters.tool_build.a2a import A2AToolBuildBackend
from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest
from tests.integration.helpers.sse import start_server
from tests.test_ting.test_a2a_tasks import RecordingVolundrFactory, RecordingVolundrPort
from ting.adapters.campaign_step_definitions import CampaignStepRunDefinitions
from ting.adapters.forge_step_sessions import ForgeStepSessions
from ting.adapters.postgres_a2a_launches import PostgresA2ALaunchReservationRepository
from ting.adapters.postgres_sagas import PostgresSagaRepository
from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.adapters.postgres_workflow_steps import PostgresWorkflowStepRepository
from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.api.a2a import create_a2a_router, resolve_a2a_launch_repo
from ting.api.a2a_card import create_agent_card_router, resolve_card_workflow_repo
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig, AuthConfig, Settings
from ting.domain.models import WorkflowCampaignStatus, WorkflowDefinition, WorkflowScope
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.services.workflow_step_engine import WorkflowStepEngine
from ting.domain.step_engine import EngineSettings
from ting.domain.workflow_snapshot import pin_workflow_personas

_A2A_PATH = "/api/v1/ting/a2a"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


class _PinnedPersonas:
    """The authoritative persona source of the test workflow: one persona, one verdict."""

    def __init__(self) -> None:
        self.config = PersonaConfig(
            name="tool-smith",
            system_prompt_template="Build the tool.",
            produces=PersonaProduces(event_type_map={"built": "tool.built"}),
        )

    def load_portable(self, persona_id: str, revision: str):  # noqa: ANN201
        current = self.load_current_portable(persona_id)
        return current if current is not None and current.revision == revision else None

    def load_current_portable(self, persona_id: str):  # noqa: ANN201
        if persona_id != self.config.name:
            return None
        return portable_persona_from_config(self.config)


def _workflow() -> WorkflowDefinition:
    """trigger -> build (tool-smith) -> end: it stays active until the member closes its step."""
    now = datetime.now(UTC)
    workflow = WorkflowDefinition(
        id=uuid4(),
        name="a2a-lifecycle",
        description="Launches an isolated A2A integration task.",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["a2a", "integration"],
            "nodes": [
                {"id": "start", "kind": "trigger", "dispatchEvent": "task.requested"},
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Lifecycle",
                    "stageMembers": [
                        {
                            "personaId": "tool-smith",
                            "model": "gpt-5.5",
                            "consumesEventTypes": ["task.requested"],
                        }
                    ],
                },
                {"id": "done", "kind": "end", "completionEvent": "task.completed"},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "start",
                    "target": "stage-1",
                    "label": "task.requested -> task.requested",
                },
                {
                    "id": "e2",
                    "source": "stage-1",
                    "target": "done",
                    "label": "tool.built -> tool.built",
                },
            ],
        },
        created_at=now,
        updated_at=now,
    )
    return pin_workflow_personas(workflow, _PinnedPersonas())


class _NullEventBus:
    async def emit(self, event) -> None:
        return None


def _step_engine(pool, campaign_repo, factory) -> WorkflowRuns:
    """The production step engine over the PostgreSQL ledger and Ting's Forge session adapter."""
    steps = PostgresWorkflowStepRepository(pool)

    async def launched(run_id) -> bool:
        return (await steps.load(run_id)).launched

    projector = WorkflowCampaignProjector(
        repo=campaign_repo, event_bus=_NullEventBus(), run_launched=launched
    )
    runs = WorkflowRuns(
        engine=WorkflowStepEngine(
            repository=steps,
            sessions=ForgeStepSessions(
                volundr_factory=factory,
                campaigns=campaign_repo,
                sagas=PostgresSagaRepository(pool),
                default_model="gpt-5.5",
            ),
            definitions=CampaignStepRunDefinitions(campaign_repo),
            settings=EngineSettings(step_deadline_seconds=600.0, session_poll_seconds=0.01),
        ),
        steps=steps,
        campaigns=campaign_repo,
        sink=projector,
        volundr_factory=factory,
    )
    projector.bind_runs(runs)
    return runs


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


async def test_a2a_task_lifecycle_runs_on_the_step_engine_over_postgres(
    txn_pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One persisted A2A task supports SendMessage, GetTask, and CancelTask."""
    forge = RecordingVolundrPort()
    factory = RecordingVolundrFactory([forge])
    workflow_repo = PostgresWorkflowRepository(txn_pool)
    campaign_repo = PostgresWorkflowCampaignRepository(txn_pool)
    launch_repo = PostgresA2ALaunchReservationRepository(txn_pool)
    workflow = await workflow_repo.save_workflow(_workflow())

    ting_app = FastAPI()
    ting_app.state.authorization = AllowAllAuthorizationAdapter()
    ting_app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    ting_app.state.workflow_runs = _step_engine(txn_pool, campaign_repo, factory)
    ting_app.include_router(create_a2a_router())
    ting_app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    ting_app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launch_repo
    ting_app.dependency_overrides[resolve_volundr_factory] = lambda: factory
    caplog.set_level(logging.INFO, logger="ting.api.a2a")

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
        task = sent.json()["result"]["task"]
        # The run started: its first step is active on the member's session.
        assert task["status"]["state"] == "TASK_STATE_WORKING"

        campaigns = await campaign_repo.list_campaigns(owner_id="a2a-integration-user")
        assert len(campaigns) == 1
        campaign = campaigns[0]
        assert campaign.slug == task["id"]
        assert campaign.status is WorkflowCampaignStatus.RUNNING
        assert campaign.active_stage_id == "stage-1"
        assert campaign.session_id == ""

        ledger = await PostgresWorkflowStepRepository(txn_pool).load(campaign.id)
        assert ledger.launched
        [step] = [item for item in ledger.steps if item.member == "tool-smith"]
        assert step.state.value == "active"
        assert step.session_id == "session-1"

        assert [request.member for request in forge.step_session_requests] == ["tool-smith"]
        assert forge.step_session_requests[0].persona_definition["id"] == "tool-smith"
        [(session_id, activation)] = forge.delivered_steps
        assert session_id == "session-1"
        assert activation["activation_id"] == str(step.id)
        assert activation["valid_verdicts"] == ["built"]
        assert "Launch then cancel this A2A integration task." in activation["instructions"]

        fetched = await client.post(
            _A2A_PATH, headers=_headers(), json=_request("GetTask", {"id": task["id"]})
        )
        assert fetched.json()["result"]["id"] == task["id"]
        assert fetched.json()["result"]["status"]["state"] == "TASK_STATE_WORKING"

        canceled = await client.post(
            _A2A_PATH, headers=_headers(), json=_request("CancelTask", {"id": task["id"]})
        )
        assert canceled.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"
        # Canceling the run stopped its sessions.
        assert forge.stopped == ["session-1"]

    stored = await campaign_repo.get_campaign(campaign.id)
    assert stored is not None
    # An A2A cancel is an operator ending, not a failure.
    assert stored.status is WorkflowCampaignStatus.CANCELED
    assert stored.metadata["a2a_canceled"] is True
    ledger = await PostgresWorkflowStepRepository(txn_pool).load(campaign.id)
    assert "engine.run.canceled" in [event.event_type for event in ledger.events]
    assert any(
        "A2A JSON-RPC SendMessage finished outcome=ok" in record.getMessage()
        for record in caplog.records
    )


async def test_ravn_a2a_tool_build_backend_commissions_real_workflow_task(
    txn_pool,
) -> None:
    """The concrete Ravn client launches a persisted Ting A2A build task.

    The backend crosses real loopback HTTP to Ting's card and JSON-RPC
    endpoints.  Ting then persists the campaign, starts the run on the step
    engine and hands the first step to its member's session.  The member never
    closes it, so the backend's bounded polling fails loudly rather than
    inventing a completed artifact.
    """
    forge = RecordingVolundrPort()
    factory = RecordingVolundrFactory([forge])
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
    ting_app.state.workflow_runs = _step_engine(txn_pool, campaign_repo, factory)
    ting_app.include_router(create_a2a_router())
    ting_app.include_router(create_agent_card_router(ting_app.state.settings.a2a))
    ting_app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_card_workflow_repo] = lambda: workflow_repo
    ting_app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    ting_app.dependency_overrides[resolve_a2a_launch_repo] = lambda: launch_repo
    ting_app.dependency_overrides[resolve_volundr_factory] = lambda: factory
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
        assert campaign.status is WorkflowCampaignStatus.RUNNING
        assert campaign.session_id == ""
        assert [request.member for request in forge.step_session_requests] == ["tool-smith"]
        assert len(forge.delivered_steps) == 1
    finally:
        await _stop_server(ting_server)
