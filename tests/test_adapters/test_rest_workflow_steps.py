"""Workflow step sessions on Forge: create with a pinned persona, and the step proxy routes."""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.forge_session_policy import match_policy
from personas.domain.persona_document import (
    persona_definition_digest,
    persona_revision_for_definition,
)
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.contributors.prompt import PromptContributor
from volundr.adapters.outbound.contributors.workflow_step_host import (
    WorkflowStepHostContributor,
)
from volundr.adapters.outbound.k8s_storage import InMemoryStorageAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.services import SessionService, StatsService

# The contract's JSON shape (phase 2, Ting -> Forge), verbatim.
STEP_ACTIVATION = {
    "activation_id": "0b8f5f0e-6d0a-4a55-9a43-3f0f1f6d2b11",
    "run_id": "5c1d7f30-3e4b-4a6e-8a52-0f6f51b0a001",
    "attempt": 1,
    "generation": 0,
    "node_id": "implement",
    "node_label": "Implement",
    "member": "coder",
    "valid_endings": ["verdict", "waiting", "done", "failed"],
    "valid_verdicts": ["implemented", "blocked"],
    "outcome_schema": None,
    "deadline_at": None,
    "instructions": "Workflow: ship it.\nStep: Implement.\nClose with workflow_step_close.",
}

REPO = "https://example.com/org/repo"


@pytest.fixture
def service(repository: InMemorySessionRepository, pod_manager: MockPodManager) -> SessionService:
    return SessionService(repository, pod_manager)


@pytest.fixture
def client(service: SessionService):
    stats_service = StatsService(InMemoryStatsRepository(), service)
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            stats_service,
            pricing_provider=InMemoryPricingProvider(),
            session_participant_service=make_session_participant_service(service),
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    app.state.storage = InMemoryStorageAdapter()
    with TestClient(app) as test_client:
        yield test_client


def _persona_document(name: str = "coder") -> dict:
    definition = {
        "name": name,
        "system_prompt_template": "Write the change.",
        "permission_mode": "read_only",
    }
    return {
        "schema_version": 1,
        "id": name,
        "revision": persona_revision_for_definition(definition),
        "definition": definition,
    }


_SESSION_BODY = {
    "name": "wf-run-coder",
    "model": "claude-sonnet-4",
    "source": {"type": "git", "repo": REPO, "branch": "main"},
}


class TestWorkflowStepSessionCreate:
    """A workflow run creates a step-host session from a pinned persona document."""

    def test_the_pinned_document_and_step_identity_are_recorded_and_exposed(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        document = _persona_document()
        run_id = STEP_ACTIVATION["run_id"]

        response = client.post(
            "/api/v1/forge/sessions",
            json={
                **_SESSION_BODY,
                "persona_definition": document,
                "workflow_step_host": True,
                "workflow_run_id": run_id,
                "workflow_member": "coder",
                "issue_id": "workflow:ship-it",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["persona_name"] == "coder"
        assert body["workflow_run_id"] == run_id
        assert body["workflow_member"] == "coder"
        assert body["workflow_step_host"] is True
        assert body["tracker_issue_id"] == "workflow:ship-it"
        stored = asyncio.run(repository.get(UUID(body["id"])))
        assert stored.workload_config["persona_definition"] == document
        assert stored.workload_config["workflow_step_host"] is True
        assert stored.workload_config["workflow_run_id"] == run_id
        assert stored.workload_config["workflow_member"] == "coder"

    def test_a_members_extra_prompt_reaches_the_session_spec_with_the_step_host_setting(
        self, repository: InMemorySessionRepository, pod_manager: MockPodManager
    ) -> None:
        """Ting's system_prompt rides the step-host create into the spec Skuld is given:
        values.session.systemPrompt, which Skuld appends after the pinned persona."""
        service = SessionService(
            repository,
            pod_manager,
            contributors=[WorkflowStepHostContributor(), PromptContributor()],
        )
        app = FastAPI()
        app.include_router(
            create_router(
                service,
                StatsService(InMemoryStatsRepository(), service),
                pricing_provider=InMemoryPricingProvider(),
                session_participant_service=make_session_participant_service(service),
            )
        )

        class _SettingsStub:
            local_mounts = LocalMountsConfig()

        app.state.settings = _SettingsStub()
        app.state.admin_settings = {}
        app.state.storage = InMemoryStorageAdapter()
        with TestClient(app) as local_client:
            response = local_client.post(
                "/api/v1/forge/sessions",
                json={
                    **_SESSION_BODY,
                    "persona_definition": _persona_document(),
                    "workflow_step_host": True,
                    "workflow_run_id": STEP_ACTIVATION["run_id"],
                    "workflow_member": "coder",
                    "system_prompt": "Only touch probe/.",
                },
            )

        assert response.status_code == 201, response.text
        _, spec = pod_manager.start_calls[-1]
        assert spec.values["session"]["systemPrompt"] == "Only touch probe/."
        assert spec.values["workflowSteps"] == {"enabled": True}

    def test_an_ordinary_session_has_no_step_identity(self, client: TestClient) -> None:
        response = client.post("/api/v1/forge/sessions", json=_SESSION_BODY)

        assert response.status_code == 201
        body = response.json()
        assert body["workflow_run_id"] is None
        assert body["workflow_member"] is None
        assert body["workflow_step_host"] is False

    def test_a_digest_the_content_does_not_match_is_refused(
        self, client: TestClient, pod_manager: MockPodManager
    ) -> None:
        document = {**_persona_document(), "digest": "sha256:" + "0" * 64}

        response = client.post(
            "/api/v1/forge/sessions", json={**_SESSION_BODY, "persona_definition": document}
        )

        assert response.status_code == 422
        assert "digest mismatch" in response.text
        assert pod_manager.start_calls == []

    def test_a_matching_digest_is_accepted_and_not_stored(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        document = _persona_document()
        pinned = {**document, "digest": persona_definition_digest(document["definition"])}

        response = client.post(
            "/api/v1/forge/sessions", json={**_SESSION_BODY, "persona_definition": pinned}
        )

        assert response.status_code == 201
        stored = asyncio.run(repository.get(UUID(response.json()["id"])))
        assert stored.workload_config["persona_definition"] == document

    def test_altered_content_under_a_content_revision_is_refused(self, client: TestClient) -> None:
        document = _persona_document()
        document["definition"] = {**document["definition"], "system_prompt_template": "Do harm."}

        response = client.post(
            "/api/v1/forge/sessions", json={**_SESSION_BODY, "persona_definition": document}
        )

        assert response.status_code == 422
        assert "does not match its content" in response.text

    def test_a_document_outside_the_persona_schema_is_refused(self, client: TestClient) -> None:
        document = _persona_document()
        document["definition"] = {**document["definition"], "allowed_tools": ["file_read"]}
        document["revision"] = "r1"

        response = client.post(
            "/api/v1/forge/sessions", json={**_SESSION_BODY, "persona_definition": document}
        )

        assert response.status_code == 422
        assert "not a valid persona document" in response.text
        assert "allowed_tools is not supported" in response.text

    def test_a_persona_name_beside_a_persona_definition_is_refused(
        self, client: TestClient, pod_manager: MockPodManager
    ) -> None:
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                **_SESSION_BODY,
                "persona_name": "coder",
                "persona_definition": _persona_document(),
            },
        )

        assert response.status_code == 422
        assert "mutually exclusive" in response.text
        assert pod_manager.start_calls == []

    def test_a_run_id_without_a_member_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/forge/sessions",
            json={**_SESSION_BODY, "workflow_run_id": STEP_ACTIVATION["run_id"]},
        )

        assert response.status_code == 422
        assert "must be sent together" in response.text

    def test_a_runs_room_is_recorded_as_the_runs_room_with_no_member(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        run_id = STEP_ACTIVATION["run_id"]

        response = client.post(
            "/api/v1/forge/sessions",
            json={
                **_SESSION_BODY,
                "workflow_run_id": run_id,
                "workflow_run_room": True,
                "issue_id": "workflow:ship-it",
            },
        )

        assert response.status_code == 201, response.text
        stored = asyncio.run(repository.get(UUID(response.json()["id"])))
        assert stored.workload_config["workflow_run_room"] is True
        assert stored.workload_config["workflow_run_id"] == run_id
        assert "workflow_member" not in stored.workload_config
        assert "workflow_step_host" not in stored.workload_config

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"workflow_run_id": STEP_ACTIVATION["run_id"], "workflow_member": "coder"},
            {"workflow_run_id": STEP_ACTIVATION["run_id"], "workflow_step_host": True},
            {"workflow_run_id": STEP_ACTIVATION["run_id"], "persona_name": "coder"},
        ],
        ids=["no-run", "member", "step-host", "persona"],
    )
    def test_a_runs_room_with_an_agent_or_no_run_is_refused(
        self, client: TestClient, extra: dict
    ) -> None:
        response = client.post(
            "/api/v1/forge/sessions",
            json={**_SESSION_BODY, "workflow_run_room": True, **extra},
        )

        assert response.status_code == 422

    def test_session_tokens_cannot_call_the_step_routes(self) -> None:
        """Ting calls the step routes; a session never does, so they stay off the allow-list."""
        base = f"/api/v1/forge/sessions/{uuid4()}/workflow/steps"

        assert match_policy("POST", base) is None
        assert match_policy("GET", f"{base}/{STEP_ACTIVATION['activation_id']}") is None


class TestWorkflowStepProxy:
    """Forge relays step calls to the session's broker, status and body unchanged."""

    @pytest.fixture
    async def running(self, service: SessionService) -> Session:
        session = await service.create_session(
            "test", "claude-sonnet-4", source=GitSource(repo=REPO, branch="main")
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)
        return session

    @staticmethod
    def _upstream(response: httpx.Response | Exception):
        patcher = patch("volundr.adapters.inbound.rest.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        if isinstance(response, Exception):
            mock_client.request.side_effect = response
        else:
            mock_client.request.return_value = response
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        return patcher, mock_client

    @pytest.mark.parametrize(
        ("upstream_status", "upstream_body"),
        [
            (202, {"activation_id": STEP_ACTIVATION["activation_id"], "state": "active"}),
            (200, {"activation_id": STEP_ACTIVATION["activation_id"], "state": "active"}),
            (409, {"detail": "session already has an open step"}),
        ],
    )
    async def test_delivery_is_posted_to_the_broker_and_its_status_is_passed_through(
        self, client: TestClient, running: Session, upstream_status: int, upstream_body: dict
    ) -> None:
        patcher, upstream = self._upstream(httpx.Response(upstream_status, json=upstream_body))
        try:
            response = client.post(
                f"/api/v1/forge/sessions/{running.id}/workflow/steps", json=STEP_ACTIVATION
            )
        finally:
            patcher.stop()

        assert response.status_code == upstream_status
        assert response.json() == upstream_body
        assert upstream.request.call_args.args == (
            "POST",
            f"http://localhost:8080/s/{running.id}/api/workflow/steps",
        )
        assert upstream.request.call_args.kwargs["json"] == STEP_ACTIVATION

    @pytest.mark.parametrize(
        "booting", [SessionStatus.CREATED, SessionStatus.STARTING, SessionStatus.PROVISIONING]
    )
    async def test_a_session_still_coming_up_answers_503_without_dialling_anything(
        self, client: TestClient, running: Session, service: SessionService, booting
    ) -> None:
        # An OpenShell sandbox's chat endpoint is Forge's own session proxy, which refused
        # the unattributable loopback call with 403 while the session was starting.
        await service._repository.update(running.with_status(booting))
        patcher, upstream = self._upstream(httpx.Response(403, json={"detail": "no"}))
        try:
            read = client.get(
                f"/api/v1/forge/sessions/{running.id}/workflow/steps/"
                f"{STEP_ACTIVATION['activation_id']}"
            )
            delivered = client.post(
                f"/api/v1/forge/sessions/{running.id}/workflow/steps", json=STEP_ACTIVATION
            )
        finally:
            patcher.stop()

        for response in (read, delivered):
            assert response.status_code == 503
            assert f"is {booting.value}: its broker is not up yet" in response.json()["detail"]
        upstream.request.assert_not_called()

    async def test_state_is_read_from_the_broker(
        self, client: TestClient, running: Session
    ) -> None:
        state = {
            "activation_id": STEP_ACTIVATION["activation_id"],
            "state": "closed",
            "ending": {"kind": "verdict", "verdict": "implemented"},
        }
        patcher, upstream = self._upstream(httpx.Response(200, json=state))
        try:
            response = client.get(
                f"/api/v1/forge/sessions/{running.id}/workflow/steps/"
                f"{STEP_ACTIVATION['activation_id']}"
            )
        finally:
            patcher.stop()

        assert response.status_code == 200
        assert response.json() == state
        assert upstream.request.call_args.args == (
            "GET",
            f"http://localhost:8080/s/{running.id}/api/workflow/steps/"
            f"{STEP_ACTIVATION['activation_id']}",
        )

    async def test_an_unknown_step_is_the_brokers_404(
        self, client: TestClient, running: Session
    ) -> None:
        patcher, _ = self._upstream(httpx.Response(404, json={"detail": "no such step"}))
        try:
            response = client.get(f"/api/v1/forge/sessions/{running.id}/workflow/steps/{uuid4()}")
        finally:
            patcher.stop()

        assert response.status_code == 404

    async def test_an_unreachable_pod_reconciles_and_409s_never_accepts(
        self, client: TestClient, running: Session
    ) -> None:
        request = httpx.Request("POST", f"http://localhost:8080/s/{running.id}/api/workflow/steps")
        patcher, _ = self._upstream(httpx.ConnectError("refused", request=request))
        try:
            response = client.post(
                f"/api/v1/forge/sessions/{running.id}/workflow/steps", json=STEP_ACTIVATION
            )
        finally:
            patcher.stop()

        assert response.status_code == 409
        assert "not delivered" in response.json()["detail"]

    def test_an_unknown_session_is_404(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/forge/sessions/{uuid4()}/workflow/steps", json=STEP_ACTIVATION
        )

        assert response.status_code == 404
