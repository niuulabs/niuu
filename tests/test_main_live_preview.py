"""Forge's live-preview wiring: origin settings, the router, and its credential issuer."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from niuu.config_models import SessionPreviewConfig
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    FORGE_SESSION_PREVIEW_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
)
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.preview_proxy import PreviewRouter, PreviewRouterMiddleware
from niuu.session_proxy import PreviewCredentialIssuerMissingError
from volundr.config import Settings
from volundr.main import PREVIEW_ROUTER_WORKLOAD, _preview_origin_template, create_app

SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
ISSUER = "https://forge.example/api/v1/tokens/workload"


@pytest.fixture(autouse=True)
def _mocked_startup(monkeypatch):
    repository = AsyncMock()
    repository.get.return_value = None
    repository.create.side_effect = lambda user: user
    monkeypatch.setattr("volundr.main.PostgresUserRepository", lambda _pool: repository)
    monkeypatch.setenv("NOTIFICATIONS__DISPATCHER__ENABLED", "false")


def _embedded_host(monkeypatch, config: SessionPreviewConfig) -> None:
    """Mini mode: the local host's niuu settings carry host.session_preview."""
    monkeypatch.setattr(
        "niuu.config.NiuuSettings",
        lambda: SimpleNamespace(host=SimpleNamespace(session_preview=config)),
    )


class TestPreviewOriginTemplate:
    def test_standalone_disabled_has_no_origin(self) -> None:
        template = _preview_origin_template(
            SessionPreviewConfig(), public_origin="http://localhost:8080", embedded=False
        )

        assert template == ""

    def test_standalone_enabled_serves_https_on_the_preview_domain(self) -> None:
        config = SessionPreviewConfig(enabled=True, domain="preview.example.com")

        template = _preview_origin_template(
            config, public_origin="http://localhost:8080", embedded=False
        )

        assert template == "https://{port}--{session_id}.preview.example.com"
        assert template.format(port=3000, session_id=SESSION) == (
            f"https://3000--{SESSION}.preview.example.com"
        )

    def test_embedded_uses_the_hosts_settings_and_the_public_origin_port(self, monkeypatch) -> None:
        _embedded_host(monkeypatch, SessionPreviewConfig(enabled=True, domain="localhost"))
        # The standalone config is ignored when embedded.
        standalone = SessionPreviewConfig(enabled=True, domain="ignored.example.com")

        template = _preview_origin_template(
            standalone, public_origin="http://localhost:8080", embedded=True
        )

        assert template == "http://{port}--{session_id}.localhost:8080"

    def test_embedded_keeps_the_public_origin_scheme_and_omits_a_missing_port(
        self, monkeypatch
    ) -> None:
        _embedded_host(monkeypatch, SessionPreviewConfig(enabled=True, domain="niuu.localhost"))

        template = _preview_origin_template(
            SessionPreviewConfig(), public_origin="https://forge.local", embedded=True
        )

        assert template == "https://{port}--{session_id}.niuu.localhost"

    def test_embedded_with_the_host_preview_disabled_has_no_origin(self, monkeypatch) -> None:
        _embedded_host(monkeypatch, SessionPreviewConfig(enabled=False))

        template = _preview_origin_template(
            SessionPreviewConfig(enabled=True, domain="preview.example.com"),
            public_origin="http://localhost:8080",
            embedded=True,
        )

        assert template == ""

    def test_embedded_default_reads_the_real_niuu_host_settings(self) -> None:
        template = _preview_origin_template(
            SessionPreviewConfig(), public_origin="http://localhost:9000", embedded=True
        )

        assert template == "http://{port}--{session_id}.localhost:9000"


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def _patches():
    """The mocked infrastructure every lifespan test in this repo stands on."""
    mock_pool = AsyncMock()
    mock_pool.get_max_size = MagicMock(return_value=10)

    @asynccontextmanager
    async def _mock_db_pool(_config):
        yield mock_pool

    return (
        patch("volundr.main._bootstrap_startup_schema", new=AsyncMock()),
        patch("volundr.main.database_pool", _mock_db_pool),
        patch(
            "volundr.adapters.outbound.bifrost_catalog_http.HttpBifrostCatalogAdapter.list_models",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "volundr.domain.services.tenant.TenantService.ensure_default_tenant",
            new=AsyncMock(),
        ),
        patch(
            "volundr.domain.services.session.SessionService.reconcile_provisioning_sessions",
            new=AsyncMock(),
        ),
        patch(
            "volundr.domain.services.session.SessionService.reconcile_active_sessions",
            new=AsyncMock(),
        ),
        patch("cli.server.get_skuld_registry", return_value=None),
    )


@pytest.fixture
def standalone_infra():
    patches = _patches()
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def _middleware_names(app) -> list[str]:
    return [m.cls.__name__ for m in app.user_middleware]


class TestLivePreviewSettingsEndpoint:
    def test_disabled_by_default(self, standalone_infra) -> None:
        app = create_app(_settings())

        with TestClient(app) as client:
            body = client.get("/api/v1/forge/settings/live-preview").json()

        assert body == {"enabled": False, "origin_template": ""}
        assert PreviewRouterMiddleware.__name__ not in _middleware_names(app)
        assert getattr(app.state, "preview_router", None) is None

    def test_standalone_enabled_publishes_the_preview_origin(self, standalone_infra) -> None:
        settings = _settings(session_preview={"enabled": True, "domain": "Preview.Example.com"})
        app = create_app(settings)

        with TestClient(app) as client:
            body = client.get("/api/v1/forge/settings/live-preview").json()

        assert body == {
            "enabled": True,
            "origin_template": "https://{port}--{session_id}.preview.example.com",
        }

    def test_embedded_forge_publishes_the_hosts_localhost_origin(self, standalone_infra) -> None:
        from niuu.session_proxy import SkuldPortRegistry

        registry = SkuldPortRegistry(dev_identity=True)
        app = create_app(
            _settings(), public_origin="http://localhost:8123", skuld_registry=registry
        )

        with TestClient(app) as client:
            body = client.get("/api/v1/forge/settings/live-preview").json()

        assert body == {
            "enabled": True,
            "origin_template": "http://{port}--{session_id}.localhost:8123",
        }
        # Embedded: the host's root app owns the router, not this app.
        assert PreviewRouterMiddleware.__name__ not in _middleware_names(app)


class TestStandaloneRouterWiring:
    def test_enabled_adds_the_outermost_middleware_and_builds_the_router(
        self, standalone_infra
    ) -> None:
        settings = _settings(session_preview={"enabled": True, "domain": "preview.example.com"})
        app = create_app(settings)

        # add_middleware prepends, so index 0 is the outermost layer.
        assert _middleware_names(app)[0] == PreviewRouterMiddleware.__name__
        with TestClient(app):
            assert isinstance(app.state.preview_router, PreviewRouter)
            router = app.state.preview_router
        assert router._client.is_closed  # closed again by the lifespan on shutdown

    def test_without_workload_identity_a_preview_cannot_mint_a_credential(
        self, standalone_infra
    ) -> None:
        settings = _settings(session_preview={"enabled": True, "domain": "preview.example.com"})
        app = create_app(settings)
        principal = Principal(user_id="u", email="", tenant_id="t", roles=[])

        with TestClient(app) as client:
            registry = app.state.session_proxy_registry
            with pytest.raises(PreviewCredentialIssuerMissingError, match="workload_identity"):
                client.portal.call(registry.preview_credential, SESSION, principal)

    def test_with_workload_identity_the_issuer_mints_a_session_bound_scoped_token(
        self, standalone_infra
    ) -> None:
        pem = WorkloadIdentityService(
            type("C", (), {"enabled": True})()
        ).private_key_pem_for_tests()
        settings = _settings(
            workload_identity={
                "enabled": True,
                "issuer": ISSUER,
                "signing_key_pem": pem,
                "audiences": ["volundr-api", "preview-aud"],
            },
            session_preview={
                "enabled": True,
                "domain": "preview.example.com",
                "credential_audience": "preview-aud",
                "credential_ttl_seconds": 120,
            },
        )
        app = create_app(settings)
        principal = Principal(user_id="user-1", email="u@x.test", tenant_id="t1", roles=["r1"])

        with TestClient(app) as client:
            registry = app.state.session_proxy_registry
            token = client.portal.call(registry.preview_credential, SESSION, principal)

        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iss"] == ISSUER
        assert claims["sub"] == "user-1"
        assert "preview-aud" in claims["aud"]
        assert claims["token_use"] == VALKYRIE_BUILD_TOKEN_USE
        assert claims["scopes"] == [FORGE_SESSION_PREVIEW_SCOPE]
        assert claims["workload_session_id"] == SESSION
        assert claims["workload_name"] == PREVIEW_ROUTER_WORKLOAD
        assert claims["exp"] - claims["iat"] == 120
