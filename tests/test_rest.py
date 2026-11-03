"""Tests for the REST API adapter."""

from datetime import UTC
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.conftest import MockEventBroadcaster, make_session_participant_service
from tests.support.push_fakes import Clock, FakeDeviceRepository
from volundr.adapters.inbound.rest import (
    _server_side_http_proxy_target,
    _server_side_ws_connect_overrides,
    create_router,
)
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import EventType, RealtimeEvent
from volundr.domain.push import (
    PushEnvironment,
    PushTokenKind,
    UnsupportedTokenKindError,
    token_fingerprint,
)
from volundr.domain.services import SessionService, StatsService


def test_server_side_ws_connect_overrides_openshell_service_host():
    overrides = _server_side_ws_connect_overrides(
        "ws://forge-8093e93dc7634efeb2c382--skuld.openshell.localhost:8080/session",
        gateway_url="http://openshell.openshell.svc.cluster.local:8080",
    )

    assert overrides == {
        "host": "openshell.openshell.svc.cluster.local",
        "port": 8080,
        "proxy": None,
    }


def test_server_side_http_proxy_target_openshell_service_host():
    url, headers = _server_side_http_proxy_target(
        "http://forge-123--skuld.openshell.localhost:8080/api/conversation/history",
        gateway_url="http://openshell.openshell.svc.cluster.local:8080",
    )

    assert url == ("http://openshell.openshell.svc.cluster.local:8080/api/conversation/history")
    assert headers == {"Host": "forge-123--skuld.openshell.localhost:8080"}


class _StubIdentity(EnvoyHeaderAuthenticationAdapter):
    """Minimal identity so _optional_principal resolves a header principal."""

    async def get_or_provision_user(self, principal):
        return None


class TestDeviceEndpoints:
    """Tests for the push device registration endpoints."""

    @pytest.fixture
    def device_repo(self):
        return FakeDeviceRepository(Clock())

    @pytest.fixture
    def client(self, repository, pod_manager, stats_repository, pricing_provider, device_repo):
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        router = create_router(
            session_service=session_service,
            stats_service=StatsService(stats_repository, session_service),
            pricing_provider=pricing_provider,
            device_repository=device_repo,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)
        return TestClient(app)

    _AUTH = {"x-auth-user-id": "user-1"}

    def test_register_then_list(self, client, device_repo):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1", "app_bundle_id": "com.niuu.forge"},
            headers=self._AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["platform"] == "ios"
        assert resp.json()["token"] == "tok-1"
        # A registration without the newer fields is a production alert token.
        assert resp.json()["kind"] == "alert"
        assert resp.json()["environment"] == "production"
        assert resp.json()["activity_id"] is None

        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert listed.status_code == 200
        assert [d["token"] for d in listed.json()] == ["tok-1"]

    def test_register_is_idempotent(self, client):
        body = {"platform": "ios", "token": "tok-1"}
        client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)
        client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert len(listed.json()) == 1

    def test_register_keeps_the_environment_and_ignores_unknown_fields(self, client, device_repo):
        resp = client.post(
            "/api/v1/forge/devices",
            json={
                "platform": "ios",
                "token": "tok-env",
                "environment": "sandbox",
                "kind": "alert",
                "unknown": "ignored",
            },
            headers=self._AUTH,
        )
        assert resp.status_code == 201
        (device,) = device_repo.devices.values()
        assert device.environment is PushEnvironment.SANDBOX

    @pytest.mark.parametrize(
        ("sent", "stored", "assumed"),
        [
            ("development", "sandbox", []),
            ("Production", "production", []),
            (None, "production", ["missing"]),
            ("staging", "production", ["unknown"]),
        ],
    )
    def test_the_environment_is_normalised_and_an_assumed_one_is_counted(
        self, repository, pod_manager, stats_repository, pricing_provider, sent, stored, assumed
    ):
        """A missing or unknown environment is stored as production and counted, not lost."""
        noted: list[str] = []
        device_repo = FakeDeviceRepository(Clock())
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                device_repository=device_repo,
                on_environment_assumed=noted.append,
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        body = {"platform": "ios", "token": "tok-1"} | ({"environment": sent} if sent else {})

        resp = TestClient(app).post("/api/v1/forge/devices", json=body, headers=self._AUTH)

        assert resp.status_code == 201
        assert resp.json()["environment"] == stored
        assert noted == assumed

    def test_register_rejects_bad_platform(self, client):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "blackberry", "token": "t"},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    def test_unregister(self, client):
        client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1"},
            headers=self._AUTH,
        )
        resp = client.request("DELETE", "/api/v1/forge/devices/tok-1", headers=self._AUTH)
        assert resp.status_code == 204
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert listed.json() == []

    def test_register_the_apps_exact_body(self, client, device_repo):
        body = {
            "platform": "ios",
            "token": "0a1b2c3d",
            "kind": "live_activity",
            "environment": "sandbox",
            "app_bundle_id": "com.xteo.lexi.chat",
            "activity_id": "ACT-1",
        }
        resp = client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)

        assert resp.status_code == 201
        stored = resp.json()
        assert {key: stored[key] for key in body} == body
        (device,) = device_repo.devices.values()
        assert device.kind is PushTokenKind.LIVE_ACTIVITY
        assert device.environment is PushEnvironment.SANDBOX
        assert device.activity_id == "ACT-1"
        assert device.owner_id == "user-1"

    def test_a_widget_fallback_token_is_stored_and_logged_by_fingerprint(
        self, client, device_repo, caplog
    ):
        caplog.set_level("INFO", logger="volundr.adapters.inbound.rest")
        resp = client.post(
            "/api/v1/forge/devices",
            json={
                "platform": "ios",
                "token": "fallback-secret",
                "kind": "widget_fallback",
                "environment": "production",
            },
            headers=self._AUTH,
        )

        assert resp.status_code == 201 and resp.json()["kind"] == "widget_fallback"
        (device,) = device_repo.devices.values()
        assert device.kind is PushTokenKind.WIDGET_FALLBACK
        assert (
            f"Registered a widget_fallback push token {token_fingerprint('fallback-secret')} "
            "(production)" in caplog.text
        )
        assert "fallback-secret" not in caplog.text

    @pytest.mark.parametrize("kind", ["alert", "live_activity_start", "widget", "widget_fallback"])
    def test_activity_id_belongs_to_live_activity_tokens_only(self, client, kind):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "t", "kind": kind, "activity_id": None},
            headers=self._AUTH,
        )
        assert resp.status_code == 201 and resp.json()["kind"] == kind

        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "t", "kind": kind, "activity_id": "ACT-9"},
            headers=self._AUTH,
        )
        assert resp.json()["activity_id"] is None

    def test_one_token_registers_once_per_kind(self, client):
        for kind in ("alert", "widget", "alert"):
            client.post(
                "/api/v1/forge/devices",
                json={"platform": "ios", "token": "tok-1", "kind": kind},
                headers=self._AUTH,
            )
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH).json()
        assert sorted(d["kind"] for d in listed) == ["alert", "widget"]

    def test_a_token_moves_to_the_user_who_registers_it_last(self, client):
        body = {"platform": "ios", "token": "tok-1"}
        client.post("/api/v1/forge/devices", json=body, headers={"x-auth-user-id": "user-1"})
        client.post("/api/v1/forge/devices", json=body, headers={"x-auth-user-id": "user-2"})

        assert (
            client.get("/api/v1/forge/devices", headers={"x-auth-user-id": "user-1"}).json() == []
        )
        second = client.get("/api/v1/forge/devices", headers={"x-auth-user-id": "user-2"}).json()
        assert [d["token"] for d in second] == ["tok-1"]

    @pytest.mark.parametrize("field", [{"kind": "fax"}, {"activity_id": "x" * 201}])
    def test_register_rejects_unknown_values(self, client, field):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "t", **field},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    def test_unregister_one_kind(self, client):
        for kind in ("alert", "widget"):
            client.post(
                "/api/v1/forge/devices",
                json={"platform": "ios", "token": "tok-1", "kind": kind},
                headers=self._AUTH,
            )
        resp = client.request(
            "DELETE", "/api/v1/forge/devices/tok-1", params={"kind": "widget"}, headers=self._AUTH
        )
        assert resp.status_code == 204
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH).json()
        assert [d["kind"] for d in listed] == ["alert"]

    def test_an_alert_only_registry_refuses_other_kinds(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        class AlertOnly(FakeDeviceRepository):
            async def upsert(self, device):
                if device.kind is not PushTokenKind.ALERT:
                    raise UnsupportedTokenKindError("alert tokens only")
                return await super().upsert(device)

        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                device_repository=AlertOnly(Clock()),
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        client = TestClient(app)
        widget = {"platform": "ios", "token": "t", "kind": "widget"}
        resp = client.post("/api/v1/forge/devices", json=widget, headers=self._AUTH)
        assert resp.status_code == 422
        assert resp.json()["detail"] == "alert tokens only"
        alert = {"platform": "ios", "token": "t"}
        resp = client.post("/api/v1/forge/devices", json=alert, headers=self._AUTH)
        assert resp.status_code == 201

    def test_install_id_reaches_the_registry_and_comes_back(self, client, device_repo):
        body = {
            "platform": "ios",
            "token": "w-1",
            "kind": "widget",
            "environment": "production",
            "install_id": "install-a",
        }

        resp = client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)

        assert resp.status_code == 201
        assert resp.json()["install_id"] == "install-a"
        (device,) = device_repo.devices.values()
        assert device.install_id == "install-a"

    @pytest.mark.parametrize(
        ("token_kinds", "advertised"),
        [(frozenset(PushTokenKind), True), (frozenset({PushTokenKind.ALERT}), False)],
    )
    def test_only_the_gateway_node_advertises_push_installs(
        self, repository, pod_manager, stats_repository, pricing_provider, token_kinds, advertised
    ):
        from volundr.config import LocalMountsConfig

        class Registry(FakeDeviceRepository):
            pass

        registry = Registry(Clock())
        registry.token_kinds = token_kinds
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                device_repository=registry,
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        app.state.settings = type("S", (), {"local_mounts": LocalMountsConfig()})()
        app.state.admin_settings = {}
        app.state.storage = type("St", (), {"supports_home_volumes": False})()

        flags = TestClient(app).get("/api/v1/forge/feature-flags").json()

        assert flags["capabilities"]["push_installs"] is advertised
        # The app registers its PushKit token only where this says so.
        assert flags["capabilities"]["push_voip"] is advertised

    def test_requires_authentication(self, client):
        # No x-auth-user-id header -> no principal -> 401.
        assert client.get("/api/v1/forge/devices").status_code == 401

    def test_owner_isolation(self, client):
        client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1"},
            headers={"x-auth-user-id": "user-1"},
        )
        other = client.get("/api/v1/forge/devices", headers={"x-auth-user-id": "user-2"})
        assert other.json() == []

    def _client_with_hook(
        self, repository, pod_manager, stats_repository, pricing_provider, device_repo, hook
    ) -> TestClient:
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                device_repository=device_repo,
                on_device_registered=hook,
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_registration_provisions_the_owners_push_rule(
        self, repository, pod_manager, stats_repository, pricing_provider, device_repo
    ):
        owners: list[str] = []

        async def hook(owner_id: str) -> None:
            owners.append(owner_id)

        client = self._client_with_hook(
            repository, pod_manager, stats_repository, pricing_provider, device_repo, hook
        )
        resp = client.post(
            "/api/v1/forge/devices", json={"platform": "ios", "token": "t"}, headers=self._AUTH
        )
        assert resp.status_code == 201
        assert owners == ["user-1"]

    def test_failed_rule_provisioning_fails_the_registration(
        self, repository, pod_manager, stats_repository, pricing_provider, device_repo
    ):
        async def hook(owner_id: str) -> None:
            raise RuntimeError("rules store down")

        client = self._client_with_hook(
            repository, pod_manager, stats_repository, pricing_provider, device_repo, hook
        )
        resp = client.post(
            "/api/v1/forge/devices", json={"platform": "ios", "token": "t"}, headers=self._AUTH
        )
        # Not acknowledged, so the app registers again; the device upsert is idempotent.
        assert resp.status_code == 500

    def test_503_when_repository_absent(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        router = create_router(
            session_service=session_service,
            stats_service=StatsService(stats_repository, session_service),
            pricing_provider=pricing_provider,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "t"},
            headers=self._AUTH,
        )
        assert resp.status_code == 503


class TestSSEEndpoint:
    """Tests for the SSE streaming endpoint."""

    def test_sse_endpoint_without_broadcaster_returns_503(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """SSE endpoint returns 503 when broadcaster is not available."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
        )
        stats_service = StatsService(stats_repository, session_service)

        # Create router without broadcaster
        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=None,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)

        client = TestClient(app)
        response = client.get("/api/v1/forge/sessions/stream")

        assert response.status_code == 503
        assert "Event streaming not available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_sse_endpoint_streams_events(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """SSE endpoint streams events from broadcaster."""
        from collections.abc import AsyncGenerator
        from datetime import datetime

        class _FiniteBroadcaster(InMemoryEventBroadcaster):
            """Yields a preset list of events then terminates.

            HTTPX's ASGITransport runs the ASGI app to completion before
            returning, so infinite SSE generators deadlock. A finite
            broadcaster lets StreamingResponse close naturally.
            """

            def __init__(self, events: list[RealtimeEvent]):
                super().__init__()
                self._preset = events

            async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
                for ev in self._preset:
                    yield ev

        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={"test": "data"},
            timestamp=datetime.now(UTC),
        )
        broadcaster = _FiniteBroadcaster([event])
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
        )
        stats_service = StatsService(stats_repository, session_service)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=broadcaster,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/forge/sessions/stream?scope=local")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers["X-Forge-Session-Scope"] == "local"
        lines = [line for line in response.text.splitlines() if line]
        assert any("event: heartbeat" in line for line in lines)

    @staticmethod
    def _scoped_stream_app(repository, pod_manager, stats_repository, pricing_provider, events):
        from collections.abc import AsyncGenerator

        from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter

        class _PresetBroadcaster(InMemoryEventBroadcaster):
            async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
                for event in events:
                    yield event

        broadcaster = _PresetBroadcaster()
        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            authorization=SimpleRoleAuthorizationAdapter(),
        )
        app = FastAPI()
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                broadcaster=broadcaster,
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        return app

    @pytest.mark.asyncio
    async def test_sse_stream_hides_other_users_sessions(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A non-owner receives no events for another user's session."""
        from datetime import datetime

        from httpx import ASGITransport, AsyncClient

        def event(event_type: EventType, **data) -> RealtimeEvent:
            return RealtimeEvent(type=event_type, data=data, timestamp=datetime.now(UTC))

        alice = {"owner_id": "alice", "tenant_id": "t1"}
        events = [
            event(EventType.SESSION_UPDATED, id="alice-session", **alice),
            event(EventType.SESSION_ACTIVITY, session_id="alice-session", state="idle", **alice),
            event(EventType.SESSION_NEEDS_INPUT, session_id="alice-session", **alice),
            event(EventType.SESSION_DELETED, id="alice-session", **alice),
            event(EventType.SESSION_UPDATED, id="bob-session", owner_id="bob", tenant_id="t1"),
            event(EventType.HEARTBEAT),
        ]
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, events
        )
        app.state.identity = _StubIdentity()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/forge/sessions/stream",
                headers={
                    "x-auth-user-id": "bob",
                    "x-auth-tenant": "t1",
                    "x-auth-roles": "volundr:developer",
                },
            )

        assert response.status_code == 200
        assert "alice-session" not in response.text
        assert "bob-session" in response.text
        assert "event: heartbeat" in response.text

    def test_sse_stream_rejects_guild_scope(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A standalone Forge cannot serve guild scope and says so."""
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, []
        )
        app.state.identity = _StubIdentity()
        response = TestClient(app).get(
            "/api/v1/forge/sessions/stream?scope=guild",
            headers={"x-auth-user-id": "bob", "x-auth-tenant": "t1"},
        )
        assert response.status_code == 422

    def test_sse_stream_requires_a_principal_when_authorization_is_configured(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, []
        )
        response = TestClient(app).get("/api/v1/forge/sessions/stream")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_stream_stats_cover_only_the_subscribers_sessions(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A stats tick reaches the subscriber as its own figures, never the publisher's."""
        from datetime import datetime

        from httpx import ASGITransport, AsyncClient

        tick = RealtimeEvent(
            type=EventType.STATS_UPDATED,
            data={"active_sessions": 1000},
            timestamp=datetime.now(UTC),
        )
        stats_repository.set_stats(active_sessions=2)
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, [tick]
        )
        app.state.identity = _StubIdentity()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/forge/sessions/stream",
                headers={
                    "x-auth-user-id": "bob",
                    "x-auth-tenant": "t1",
                    "x-auth-roles": "volundr:developer",
                },
            )

        assert response.status_code == 200
        assert "event: stats_updated" in response.text
        assert '"active_sessions": 2' in response.text
        assert "1000" not in response.text
        assert stats_repository.scopes == [("t1", "bob")]

    def test_router_refuses_a_stream_without_stats(self, repository, pod_manager, pricing_provider):
        """The stream cannot serve stats_updated without a stats service, so say so."""
        broadcaster = InMemoryEventBroadcaster()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        with pytest.raises(ValueError, match="stats_service"):
            create_router(
                session_service=session_service,
                pricing_provider=pricing_provider,
                broadcaster=broadcaster,
                session_participant_service=make_session_participant_service(session_service),
            )


class TestStatsScope:
    """GET /stats is bounded like GET /sessions."""

    @staticmethod
    def _client(repository, pod_manager, stats_repository, *, identity: bool) -> TestClient:
        from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            authorization=SimpleRoleAuthorizationAdapter(),
        )
        app = FastAPI()
        if identity:
            app.state.identity = _StubIdentity()
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                session_participant_service=make_session_participant_service(session_service),
            )
        )
        return TestClient(app)

    @pytest.mark.parametrize(
        ("roles", "scope"),
        [("volundr:developer", ("t1", "bob")), ("volundr:admin", ("t1", None))],
    )
    def test_stats_are_bounded_to_the_callers_sessions(
        self, repository, pod_manager, stats_repository, roles, scope
    ):
        client = self._client(repository, pod_manager, stats_repository, identity=True)
        response = client.get(
            "/api/v1/forge/stats",
            headers={"x-auth-user-id": "bob", "x-auth-tenant": "t1", "x-auth-roles": roles},
        )
        assert response.status_code == 200
        assert stats_repository.scopes == [scope]

    def test_stats_require_a_principal_when_authorization_is_configured(
        self, repository, pod_manager, stats_repository
    ):
        client = self._client(repository, pod_manager, stats_repository, identity=False)
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 401
        assert stats_repository.scopes == []


class TestSessionEndpoints:
    """Tests for session CRUD endpoints."""

    @pytest.fixture
    def mock_broadcaster(self):
        """Create a mock broadcaster for testing."""
        return MockEventBroadcaster()

    @pytest.fixture
    def app(self, repository, pod_manager, stats_repository, pricing_provider, mock_broadcaster):
        """Create a FastAPI app with the router."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=mock_broadcaster,
        )
        stats_service = StatsService(stats_repository, session_service)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=mock_broadcaster,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    def test_create_session(self, client, mock_broadcaster):
        """Creating a session via API creates and starts it, publishes events."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-session"
        assert data["status"] == "starting"

        # Verify events were published (created + updated for starting + updated for provisioning)
        assert len(mock_broadcaster.session_created_events) == 1

    @pytest.mark.parametrize("query", ["scope=local", "scope=local&status=archived", "scope=guild"])
    def test_direct_session_scope_acknowledged(self, client, query):
        response = client.get("/api/v1/forge/sessions?" + query)
        assert response.status_code == 200
        assert response.headers["X-Forge-Session-Scope"] == "local"

    def test_direct_session_scope_rejects_typo(self, client):
        assert client.get("/api/v1/forge/sessions?scope=locla").status_code == 422

    def test_list_sessions(self, client):
        """List sessions endpoint returns empty list initially."""
        response = client.get("/api/v1/forge/sessions")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_session_not_found(self, client):
        """Getting a non-existent session returns 404."""
        response = client.get("/api/v1/forge/sessions/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404

    def test_update_session(self, client, mock_broadcaster):
        """Updating a session via API publishes event."""
        # Create session first
        create_response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )
        session_id = create_response.json()["id"]

        # Clear created events
        mock_broadcaster._session_updated_events.clear()

        # Update session
        response = client.put(
            f"/api/v1/forge/sessions/{session_id}",
            json={"name": "updated-name"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "updated-name"

        # Verify event was published
        assert len(mock_broadcaster.session_updated_events) == 1

    def test_delete_session(self, client, mock_broadcaster):
        """Deleting a session via API publishes event."""
        # Create session first
        create_response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )
        session_id = create_response.json()["id"]

        # Delete session
        response = client.delete(f"/api/v1/forge/sessions/{session_id}")

        assert response.status_code == 204

        # Verify event was published
        assert len(mock_broadcaster.session_deleted_events) == 1


class TestStatsEndpoint:
    """Tests for the stats endpoint."""

    @pytest.fixture
    def app(self, repository, pod_manager, stats_repository, pricing_provider):
        """Create a FastAPI app with the router."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
        )
        stats_service = StatsService(stats_repository, session_service)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            session_participant_service=make_session_participant_service(session_service),
        )
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    def test_get_stats(self, client, stats_repository):
        """Stats endpoint returns current statistics."""
        stats_repository.set_stats(
            active_sessions=3,
            total_sessions=10,
            tokens_today=5000,
            local_tokens=1000,
            cloud_tokens=4000,
            cost_today=Decimal("2.50"),
        )

        response = client.get("/api/v1/forge/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["total_sessions"] == 10
        assert data["tokens_today"] == 5000
        assert data["cost_today"] == 2.50
