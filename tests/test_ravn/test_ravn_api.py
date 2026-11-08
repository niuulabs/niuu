"""Tests for the Ravn FastAPI sub-application."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient as _TestClient

from niuu.domain.discovered_residents import discovered_resident_id
from ravn.api import create_app
from sleipnir.domain.events import SleipnirEvent


def TestClient(app, **kwargs):  # noqa: N802 — test client factory
    """Simulate identity headers supplied by the trusted Envoy proxy."""
    return _TestClient(
        app,
        headers={
            "x-auth-user-id": "dev-user",
            "x-auth-tenant": "default",
            "x-auth-roles": "volundr:developer",
        },
        **kwargs,
    )


class FakeWardenDeployer:
    def __init__(self, *, service_path: Path, fail_on: str = "") -> None:
        self._service_path = service_path
        self._fail_on = fail_on


def _valkyrie_catalog() -> str:
    return json.dumps(
        {
            "environments": [
                {
                    "id": "valhalla",
                    "name": "Valhalla k8s",
                    "kind": "kubernetes",
                    "health": "watch",
                    "flockId": "flock-k8s",
                    "flock": {
                        "name": "K8s Valkyrie flock",
                        "domain": "kubernetes operations",
                        "natsSubject": "flock.k8s.>",
                    },
                    "valkyrie": {
                        "valkyrieId": "valkyrie-valhalla-k8s",
                        "valkyrieName": "Sigrun",
                        "persona": "k8s-valkyrie",
                        "specialty": "cluster event triage and flock learning exchange",
                        "confidence": 0.82,
                    },
                    "transport": {
                        "account": "obs-valhalla",
                        "streamName": "obs-valhalla-events",
                        "subjectPrefix": "obs.valhalla",
                    },
                }
            ]
        }
    )


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", _valkyrie_catalog())
    return TestClient(create_app())


@pytest.fixture
def client_with_personas(tmp_path) -> TestClient:
    """TestClient wired with a filesystem persona loader."""

    from personas.adapters.loader import FilesystemPersonaAdapter

    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    loader = FilesystemPersonaAdapter(persona_dirs=[str(persona_dir)], include_builtin=True)
    return TestClient(create_app(persona_loader=loader))


@respx.mock
def test_status_endpoint_reports_live_counts(client: TestClient):
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://localhost:8080/api/v1/forge/sessions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "ravn-flock",
                    "name": "Ravn flock",
                    "status": "running",
                    "workload_type": "ravn_flock",
                }
            ],
        )
    )

    resp = client.get("/api/v1/ravn/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "service": "ravn",
        "session_count": 1,
        # No managed, discovered or rostered residents.
        "fleet_member_count": 0,
        "healthy": True,
    }


@respx.mock
def test_status_endpoint_reports_discovery_unavailable(client: TestClient):
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://localhost:8080/api/v1/forge/sessions").mock(return_value=httpx.Response(503))

    resp = client.get("/api/v1/ravn/status")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Ravn runtime discovery is unavailable"


class _FailingStandaloneDiscovery:
    """Simulates a broken standalone-resident discovery adapter chain."""

    async def list_residents(self) -> list:
        raise RuntimeError("discovery adapter unreachable")


@respx.mock
def test_status_endpoint_is_not_healthy_when_a_managed_runtimes_native_sessions_fail() -> None:
    """list_ravens does not call list_resident_sessions, so a managed
    runtime's own native-session listing failing does not raise there —
    only list_sessions sees it, previously via a swallow that just skipped
    the runtime with a warning log. The old behavior therefore reported
    "healthy": True with an undercount: fewer sessions, no trace of why. It
    must be additively reported instead, on both the body and the
    X-Niuu-Source-Failures header."""
    runtime = {
        "id": "runtime-flaky",
        "name": "Flaky Resident",
        "capabilities": ["session.list"],
    }
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(200, json=[runtime])
    )
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes/runtime-flaky/sessions").mock(
        return_value=httpx.Response(500)
    )
    respx.get("http://localhost:8080/api/v1/forge/sessions").mock(
        return_value=httpx.Response(200, json=[])
    )
    app_client = TestClient(create_app())

    resp = app_client.get("/api/v1/ravn/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    failures = json.loads(resp.headers["X-Niuu-Source-Failures"])
    assert failures[0]["instanceId"] == "runtime-flaky"
    assert failures[0]["name"] == "Flaky Resident"


@respx.mock
def test_get_session_endpoint_fails_loud_when_standalone_discovery_fails() -> None:
    """A single-session lookup that falls through to standalone discovery
    must not silently answer "not found" when discovery itself is down —
    that would misreport an unreachable resident as one that never existed."""
    respx.get("http://localhost:8080/api/v1/forge/sessions/missing-session").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes/missing-session").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(200, json=[])
    )
    app_client = TestClient(create_app(resident_discovery=_FailingStandaloneDiscovery()))

    resp = app_client.get("/api/v1/ravn/sessions/missing-session")

    assert resp.status_code == 503
    assert "discovery adapter unreachable" in resp.json()["detail"]


def test_list_sessions_returns_live_ravn_sessions(client: TestClient):
    import httpx
    import respx

    # /sessions is real discovery now — it proxies the Forge sessions API and
    # keeps only ravn workloads (flock rooms) with their chat endpoints.
    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:8080/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "11111111-2222-4333-8444-555555555555",
                        "name": "research campaign",
                        "status": "running",
                        "model": "claude-opus-4-8",
                        "chat_endpoint": "ws://host/s/1/session",
                        "workload_type": "ravn_flock",
                        "created_at": "2026-07-04T10:00:00Z",
                    },
                    {
                        "id": "22222222-2222-4333-8444-555555555555",
                        "status": "running",
                        "model": "gpt-5.5",
                        "chat_endpoint": None,
                        "workload_type": "session",
                        "created_at": "2026-07-04T10:00:00Z",
                    },
                ],
            )
        )
        router.get("http://localhost:8080/api/v1/forge/resident-runtimes").respond(json=[])
        resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Only the ravn workload survives; plain coding sessions are excluded.
    assert len(data) == 1
    assert data[0]["ravn_id"] == "11111111-2222-4333-8444-555555555555"
    assert data[0]["status"] == "running"
    assert data[0]["chat_endpoint"] == "ws://host/s/1/session"


def test_get_native_session_uses_owning_resident_hint(client: TestClient):
    import respx

    session_id = "11111111-2222-4333-8444-555555555555"
    first_ravn = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    second_ravn = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    runtimes = [
        {
            "id": first_ravn,
            "name": "first",
            "capabilities": ["session.list"],
        },
        {
            "id": second_ravn,
            "name": "second",
            "capabilities": ["session.list"],
        },
    ]
    native = {
        "id": session_id,
        "title": "shared native id",
        "status": "running",
        "model": "niuu/Qwen/Qwen3.6-35B-A3B-FP8",
        "createdAt": "2026-07-12T01:00:00Z",
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://localhost:8080/api/v1/forge/sessions/{session_id}").respond(404)
        router.get(f"http://localhost:8080/api/v1/forge/resident-runtimes/{session_id}").respond(
            404
        )
        router.get("http://localhost:8080/api/v1/forge/resident-runtimes").respond(json=runtimes)
        first_route = router.get(
            f"http://localhost:8080/api/v1/forge/resident-runtimes/{first_ravn}/sessions"
        ).respond(json=[native])
        second_route = router.get(
            f"http://localhost:8080/api/v1/forge/resident-runtimes/{second_ravn}/sessions"
        ).respond(json=[native])

        response = client.get(
            f"/api/v1/ravn/sessions/{session_id}", params={"ravn_id": second_ravn}
        )

    assert response.status_code == 200
    assert response.json()["ravn_id"] == second_ravn
    assert not first_route.called
    assert second_route.called


def test_resident_create_lifecycle_and_delete_proxy_target_control_plane(client: TestClient):
    import httpx
    import respx

    runtime = {
        "id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "name": "Muninn",
        "personaName": "product-steward",
        "model": "gpt-5.6",
        "backend": "helmrelease",
        "engine": "ravn",
        "profileId": "ravn-helm",
        "desiredState": "running",
        "observedState": "deploying",
        "backendRef": {"kind": "HelmRelease", "name": "resident-id"},
        "endpoints": [{"kind": "chat", "protocol": "skuld-v1", "url": "/s/id/session"}],
        "capabilities": ["chat", "runtime.suspend"],
        "conditions": [],
    }
    with respx.mock(assert_all_called=False) as router:
        create_route = router.post("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
            return_value=httpx.Response(201, json=runtime)
        )
        suspend_route = router.post(
            "http://localhost:8080/api/v1/forge/resident-runtimes/"
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/suspend"
        ).mock(return_value=httpx.Response(200, json=runtime))
        delete_route = router.delete(
            "http://localhost:8080/api/v1/forge/resident-runtimes/"
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        ).mock(return_value=httpx.Response(204))

        created = client.post(
            "/api/v1/ravn/ravens",
            json={
                "name": "Muninn",
                "profile_id": "ravn-helm",
                "flock_id": "11111111-1111-4111-8111-111111111111",
                "flock_member_id": "22222222-2222-4222-8222-222222222222",
                "flock_role": "coordinator",
                "flock_peer_id": "ravn-muninn",
            },
        )
        suspended = client.post("/api/v1/ravn/ravens/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/suspend")
        deleted = client.delete("/api/v1/ravn/ravens/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

    assert created.status_code == 201
    assert created.json()["managed"] is True
    assert created.json()["endpoints"] == runtime["endpoints"]
    assert create_route.calls.last.request.read() == (
        b'{"name":"Muninn","profile_id":"ravn-helm","persona_name":"","model":"",'
        b'"flock_id":"11111111-1111-4111-8111-111111111111",'
        b'"flock_member_id":"22222222-2222-4222-8222-222222222222",'
        b'"flock_role":"coordinator","flock_peer_id":"ravn-muninn","realm_id":null}'
    )
    assert suspended.status_code == 200
    assert deleted.status_code == 204
    assert create_route.called and suspend_route.called and delete_route.called


@respx.mock
def test_stop_session_forwards_to_forge_with_caller_identity(client: TestClient):
    route = respx.post("http://localhost:8080/api/v1/forge/sessions/my-session-id/stop").mock(
        return_value=httpx.Response(
            200,
            json={"session_id": "my-session-id", "status": "stopped"},
        )
    )

    resp = client.post(
        "/api/v1/ravn/sessions/my-session-id/stop",
        headers={"x-auth-user-id": "user-1"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"session_id": "my-session-id", "status": "stopped"}
    assert route.calls.last.request.headers["x-auth-user-id"] == "user-1"


@respx.mock
def test_stop_session_reports_forge_unavailable(client: TestClient):
    respx.post("http://localhost:8080/api/v1/forge/sessions/my-session-id/stop").mock(
        return_value=httpx.Response(503)
    )

    resp = client.post("/api/v1/ravn/sessions/my-session-id/stop")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Forge session lifecycle is unavailable"


@respx.mock
def test_stop_session_preserves_forge_state_conflict(client: TestClient):
    respx.post("http://localhost:8080/api/v1/forge/sessions/not-running/stop").mock(
        return_value=httpx.Response(409, json={"detail": "Session cannot stop from created"})
    )

    resp = client.post("/api/v1/ravn/sessions/not-running/stop")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Session cannot stop from created"


@respx.mock
def test_stop_session_does_not_fake_success_for_unknown_session(client: TestClient):
    respx.post("http://localhost:8080/api/v1/forge/sessions/unknown/stop").mock(
        return_value=httpx.Response(404)
    )

    resp = client.post("/api/v1/ravn/sessions/unknown/stop")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Forge-backed Ravn session not found"


def test_personas_not_mounted_without_loader(client: TestClient):
    """Persona routes should not exist when no loader is provided."""
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 404


def test_personas_mounted_with_loader(client_with_personas: TestClient):
    """Persona list endpoint exists and returns 200 when a loader is wired."""
    resp = client_with_personas.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_app_no_args_returns_fastapi():

    assert isinstance(create_app(), FastAPI)


def test_sessions_dial_platform_base_url_from_settings():
    """gateway.platform.base_url in Settings is the forge API the directory calls."""
    from ravn.config import Settings

    settings = Settings(gateway={"platform": {"base_url": "http://forge.configured.internal:9999"}})
    client = TestClient(create_app(settings=settings))
    with respx.mock:
        route = respx.get("http://forge.configured.internal:9999/api/v1/forge/sessions").respond(
            json=[]
        )
        resident_route = respx.get(
            "http://forge.configured.internal:9999/api/v1/forge/resident-runtimes"
        ).respond(json=[])
        resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200
    assert resp.json() == []
    assert route.called
    assert resident_route.called


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_resident_list_answers_the_platforms_refusal_of_the_caller(client: TestClient, status):
    """A credential the platform Forge refuses is the caller's answer, never a 500.

    Ymir's Observatory polled this with a site principal that has no local tenant
    membership; the Forge's 401 surfaced as an unhandled 500 for weeks.
    """
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(status, json={"detail": "Local tenant membership is required"})
    )

    resp = client.get("/api/v1/ravn/ravens")

    assert resp.status_code == status
    assert resp.json() == {"detail": "Local tenant membership is required"}


@respx.mock
def test_resident_list_reports_a_platform_failure_as_a_bad_gateway(client: TestClient):
    respx.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
        return_value=httpx.Response(500, text="boom")
    )

    resp = client.get("/api/v1/ravn/ravens")

    assert resp.status_code == 502
    assert resp.json() == {
        "detail": "platform answered HTTP 500 for /api/v1/forge/resident-runtimes"
    }


def test_resident_list_rejects_an_unknown_visibility(client: TestClient):
    resp = client.get("/api/v1/ravn/ravens", params={"visibility": "everyone"})

    assert resp.status_code == 422


class _FakeResidentLogs:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.placements: list[object] = []

    async def read_logs(self, placement, *, lines, sources, min_level):
        if self.failure is not None:
            raise self.failure
        self.placements.append(placement)
        return {"entries": [{"timestampMs": 1, "message": "ready"}], "bufferTotal": 1}

    async def aclose(self) -> None:
        return None


def _placed_client(monkeypatch, logs: _FakeResidentLogs | None) -> TestClient:
    catalog = json.loads(_valkyrie_catalog())
    catalog["environments"][0]["valkyrie"]["placement"] = {
        "cluster": "valhalla",
        "namespace": "nats",
        "workload": "valkyrie-valhalla-k8s",
    }
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", json.dumps(catalog))
    return TestClient(create_app(resident_logs=logs))


_VALHALLA_RECORD = str(discovered_resident_id("valkyrie-valhalla-k8s"))
_FORGE_RUNTIMES = "http://localhost:8080/api/v1/forge/resident-runtimes"


def _discovered_record() -> dict:
    return {
        "id": _VALHALLA_RECORD,
        "name": "Sigrun",
        "backend": "discovered",
        "engine": "ravn",
        "backendRef": {"key": "valkyrie-valhalla-k8s"},
        "capabilities": ["chat", "session.list", "session.create", "session.delete"],
        "observedState": "active",
    }


def _runtime_started(valkyrie_id: str, memory: list[dict[str, str]]) -> SleipnirEvent:
    return SleipnirEvent(
        event_type="valkyrie.runtime.started",
        source="ravn:valkyrie:valhalla",
        payload={
            "environment_id": "valhalla",
            "valkyrie_id": valkyrie_id,
            "valkyrie_name": "Regin",
            "charter": "Deliver tickets.",
            "memory": memory,
        },
        summary="runtime started",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
    )
