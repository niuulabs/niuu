"""Forge refuses a session whose persona its model's harness cannot play, before it exists."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personas.domain.persona_document import persona_revision_for_definition
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.k8s_storage import InMemoryStorageAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.ports import PersonaPlayabilityPort, Playability
from volundr.domain.services import SessionService, StatsService

REPO = "https://example.com/org/repo"


class _Harness(PersonaPlayabilityPort):
    """Plays every persona unless given reasons it cannot, and records what it was asked."""

    def __init__(self) -> None:
        self.reasons: tuple[str, ...] = ()
        self.checked: list[tuple[str, str]] = []

    def check(self, model, persona, *, definition=None) -> Playability:
        self.checked.append((model, str(persona.get("id"))))
        return Playability(model, definition or "skuldCodex", "codex_ws", self.reasons)


@pytest.fixture
def service(repository: InMemorySessionRepository, pod_manager: MockPodManager) -> SessionService:
    return SessionService(repository, pod_manager)


@pytest.fixture
def harness() -> _Harness:
    return _Harness()


@pytest.fixture
def client(service: SessionService, harness: _Harness):
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            StatsService(InMemoryStatsRepository(), service),
            pricing_provider=InMemoryPricingProvider(),
            session_participant_service=make_session_participant_service(service),
            persona_playability=harness,
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
    definition = {"name": name, "system_prompt_template": "Write the change."}
    return {
        "schema_version": 1,
        "id": name,
        "revision": persona_revision_for_definition(definition),
        "definition": definition,
    }


_SESSION_BODY = {
    "name": "wf-run-coder",
    "model": "gpt-5.6-terra",
    "source": {"type": "git", "repo": REPO, "branch": "main"},
}


def test_a_persona_its_harness_cannot_play_is_refused_before_anything_starts(
    client: TestClient,
    harness: _Harness,
    pod_manager: MockPodManager,
    repository: InMemorySessionRepository,
) -> None:
    harness.reasons = ("permission_mode:prompt: no unattended session can ask",)

    response = client.post(
        "/api/v1/forge/sessions",
        json={**_SESSION_BODY, "persona_definition": _persona_document()},
    )

    assert response.status_code == 422
    assert "persona 'coder' cannot run on model" in response.text
    assert "permission_mode:prompt" in response.text
    assert pod_manager.start_calls == []
    assert asyncio.run(repository.list()) == []


def test_a_session_without_a_persona_is_not_checked(client: TestClient, harness: _Harness) -> None:
    harness.reasons = ("would refuse",)

    response = client.post("/api/v1/forge/sessions", json=_SESSION_BODY)

    assert response.status_code == 201
    assert harness.checked == []


def test_the_route_answers_for_every_member_without_starting_anything(
    client: TestClient, harness: _Harness, pod_manager: MockPodManager
) -> None:
    harness.reasons = ("Codex cannot do it",)

    response = client.post(
        "/api/v1/forge/sessions/playability",
        json={
            "members": [
                {
                    "member": "reviewer",
                    "model": "gpt-5.6-terra",
                    "persona_definition": _persona_document("reviewer"),
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "members": [
            {
                "member": "reviewer",
                "model": "gpt-5.6-terra",
                "definition": "skuldCodex",
                "transport": "codex_ws",
                "playable": False,
                "reasons": ["Codex cannot do it"],
            }
        ]
    }
    assert harness.checked == [("gpt-5.6-terra", "reviewer")]
    assert pod_manager.start_calls == []


def test_a_forge_without_a_checker_refuses_rather_than_guessing(
    service: SessionService,
) -> None:
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            StatsService(InMemoryStatsRepository(), service),
            pricing_provider=InMemoryPricingProvider(),
            session_participant_service=make_session_participant_service(service),
        )
    )
    with TestClient(app) as unchecked:
        response = unchecked.post(
            "/api/v1/forge/sessions/playability",
            json={
                "members": [
                    {
                        "member": "coder",
                        "model": "m",
                        "persona_definition": {"id": str(uuid4())},
                    }
                ]
            },
        )

    assert response.status_code == 422
    assert "no persona playability checker is configured" in response.text
