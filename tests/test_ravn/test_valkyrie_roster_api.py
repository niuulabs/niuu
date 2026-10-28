"""The resident routes list the configured Valkyrie catalog.

Drives the real ``create_app`` wiring: the environment catalog is read from
``RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON``, the live state comes from the
dashboard projection the app builds, and the platform (Forge) is mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient as _TestClient

import ravn.api as ravn_api
from ravn.api import create_app
from ravn.api.valkyrie_projection import ValkyrieDashboardProjection
from sleipnir.domain.events import SleipnirEvent


def TestClient(app):  # noqa: N802 — test client factory
    """Simulate identity headers supplied by the trusted Envoy proxy."""
    return _TestClient(
        app,
        headers={
            "x-auth-user-id": "dev-user",
            "x-auth-tenant": "default",
            "x-auth-roles": "volundr:developer",
        },
    )


_PLATFORM = "http://localhost:8080"
_RUNA_ROOM = "wss://yggdrasil.niuu.world/rooms/runa/ws/ravn"


def _catalog() -> str:
    return json.dumps(
        {
            "environments": [
                {
                    "id": "ymir",
                    "name": "Ymir",
                    "valkyrie": {
                        "valkyrieId": "valkyrie-runa",
                        "valkyrieName": "Runa",
                        "persona": "k8s-valkyrie",
                        "chatEndpoint": _RUNA_ROOM,
                    },
                },
                {
                    "id": "vanaheim",
                    "name": "Vanaheim",
                    "valkyrie": {
                        "valkyrieId": "valkyrie-bryn",
                        "valkyrieName": "Bryn",
                        "persona": "harvester-valkyrie",
                    },
                },
                {
                    "id": "private",
                    "name": "Private",
                    "valkyrie": {
                        "valkyrieId": "valkyrie-kara",
                        "valkyrieName": "Kara",
                        "persona": "home-valkyrie",
                        "chatEndpoint": "wss://yggdrasil.niuu.world/rooms/kara/ws/ravn",
                        "visibility": "user",
                        "ownerId": "someone-else",
                    },
                },
            ]
        }
    )


class _ObservedRuna(ValkyrieDashboardProjection):
    """Projection that has already seen telemetry from Runa."""

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.record_event(
            SleipnirEvent(
                event_type="valkyrie.presence.heartbeat",
                source="ravn:valkyrie:ymir",
                payload={"environment_id": "ymir", "valkyrie_id": "valkyrie-runa"},
                summary="heartbeat",
                urgency=0.1,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
            )
        )


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", _catalog())
    monkeypatch.setattr(ravn_api, "ValkyrieDashboardProjection", _ObservedRuna)
    return TestClient(create_app())


def _mock_forge(*, sessions: list[dict] | None = None) -> None:
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{_PLATFORM}/api/v1/forge/sessions").mock(
        return_value=httpx.Response(200, json=sessions or [])
    )


@respx.mock
def test_ravens_lists_the_catalog_with_observed_state(client: TestClient):
    _mock_forge()

    response = client.get("/api/v1/ravn/ravens")

    assert response.status_code == 200
    ravens = {raven["id"]: raven for raven in response.json()}
    assert sorted(ravens) == ["valkyrie-bryn", "valkyrie-runa"]

    runa = ravens["valkyrie-runa"]
    assert runa["resident_name"] == "Runa"
    assert runa["persona_name"] == "k8s-valkyrie"
    assert runa["kind"] == "resident"
    assert runa["deployment"] == "valkyrie"
    assert runa["location"] == "Ymir"
    assert runa["status"] == "active"
    assert runa["observed_state"] == "active"
    assert runa["desired_state"] == "running"
    assert runa["chat_endpoint"] == _RUNA_ROOM
    assert runa["capabilities"] == ["chat"]
    assert runa["conditions"][0]["reason"] == "Fresh"

    bryn = ravens["valkyrie-bryn"]
    assert bryn["status"] == "idle"
    assert bryn["observed_state"] == "idle"
    assert bryn["chat_endpoint"] is None
    assert bryn["capabilities"] == []
    assert bryn["conditions"][0]["reason"] == "NeverObserved"


@respx.mock
def test_get_raven_returns_a_visible_catalog_valkyrie(client: TestClient):
    _mock_forge()
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes/valkyrie-runa").mock(
        return_value=httpx.Response(404)
    )

    response = client.get("/api/v1/ravn/ravens/valkyrie-runa")

    assert response.status_code == 200
    assert response.json()["resident_name"] == "Runa"


@respx.mock
def test_get_raven_404s_for_a_valkyrie_the_caller_may_not_see(client: TestClient):
    _mock_forge()
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes/valkyrie-kara").mock(
        return_value=httpx.Response(404)
    )

    response = client.get("/api/v1/ravn/ravens/valkyrie-kara")

    assert response.status_code == 404


@respx.mock
def test_sessions_lists_only_valkyries_with_a_room(client: TestClient):
    _mock_forge()

    response = client.get("/api/v1/ravn/sessions")

    assert response.status_code == 200
    sessions = response.json()
    assert [session["id"] for session in sessions] == ["valkyrie-runa"]
    assert sessions[0]["chat_endpoint"] == _RUNA_ROOM
    assert sessions[0]["status"] == "running"


@respx.mock
def test_get_session_returns_the_room_channel(client: TestClient):
    _mock_forge()
    respx.get(f"{_PLATFORM}/api/v1/forge/sessions/valkyrie-runa").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes/valkyrie-runa").mock(
        return_value=httpx.Response(404)
    )

    response = client.get("/api/v1/ravn/sessions/valkyrie-runa")

    assert response.status_code == 200
    assert response.json()["chat_endpoint"] == _RUNA_ROOM


@respx.mock
def test_resident_sessions_listing_yields_the_room(client: TestClient):
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes/valkyrie-runa").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{_PLATFORM}/api/v1/forge/resident-runtimes/valkyrie-bryn").mock(
        return_value=httpx.Response(404)
    )

    runa = client.get("/api/v1/ravn/ravens/valkyrie-runa/sessions")
    bryn = client.get("/api/v1/ravn/ravens/valkyrie-bryn/sessions")

    assert [session["chat_endpoint"] for session in runa.json()] == [_RUNA_ROOM]
    assert bryn.json() == []


@respx.mock
def test_status_counts_the_catalog_in_the_fleet(client: TestClient):
    _mock_forge()

    response = client.get("/api/v1/ravn/status")

    assert response.status_code == 200
    body = response.json()
    assert body["fleet_member_count"] == 2
    assert body["session_count"] == 1
    assert body["healthy"] is True


def test_malformed_chat_endpoint_fails_startup(monkeypatch):
    catalog = json.dumps(
        [{"id": "ymir", "valkyrie": {"valkyrieId": "valkyrie-runa", "chatEndpoint": "ws://x"}}]
    )
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", catalog)

    with pytest.raises(ValueError, match="chatEndpoint"):
        create_app()


def test_a_roster_can_be_injected_in_place_of_the_catalog(monkeypatch):
    monkeypatch.delenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", raising=False)

    class _Roster:
        async def list_residents(self):
            return []

    with respx.mock:
        _mock_forge()
        response = TestClient(create_app(resident_roster=_Roster())).get("/api/v1/ravn/ravens")

    assert response.json() == []
