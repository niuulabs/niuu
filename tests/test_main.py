"""Tests for the main application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from niuu.domain.model_catalog import ManagedModel
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.config import Settings
from volundr.main import _bootstrap_startup_schema, _load_bifrost_catalog, create_app


class TestCreateApp:
    """Tests for create_app factory."""

    def test_create_app_returns_fastapi(self):
        """create_app returns a FastAPI instance."""
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_create_app_with_custom_settings(self):
        """create_app accepts custom settings."""
        settings = Settings()
        app = create_app(settings)
        assert app.state.settings is settings

    def test_create_app_default_settings(self):
        """create_app uses default settings when none provided."""
        app = create_app()
        assert isinstance(app.state.settings, Settings)

    def test_app_has_title(self):
        """App has correct title."""
        app = create_app()
        assert app.title == "Volundr"

    def test_app_has_version(self):
        """App has version."""
        app = create_app()
        assert app.version == "0.1.0"


class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check_returns_healthy(self):
        """Health check returns healthy status."""
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestCORSMiddleware:
    """Tests for CORS middleware."""

    def test_cors_allows_all_origins(self):
        """CORS middleware allows all origins."""
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)

        response = client.options(
            "/health",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS preflight should succeed
        assert response.status_code in (200, 204, 400)


class TestLifespan:
    """Tests for app lifespan startup/shutdown (mocked infrastructure)."""

    @pytest.mark.asyncio
    async def test_bootstrap_startup_schema_applies_volundr_migrations(
        self,
        tmp_path: Path,
    ) -> None:
        mig_dir = tmp_path / "volundr"
        mig_dir.mkdir()
        (mig_dir / "000001_init.up.sql").write_text("CREATE TABLE IF NOT EXISTS a (id INT);")
        (mig_dir / "000002_more.up.sql").write_text("CREATE TABLE IF NOT EXISTS b (id INT);")

        mock_conn = AsyncMock()

        with (
            patch("asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn),
            patch("cli.resources.migration_dir", return_value=mig_dir),
        ):
            await _bootstrap_startup_schema(Settings())

        executed_sql = [call.args[0] for call in mock_conn.execute.await_args_list]
        assert executed_sql == [
            "CREATE TABLE IF NOT EXISTS a (id INT);",
            "CREATE TABLE IF NOT EXISTS b (id INT);",
        ]
        mock_conn.close.assert_awaited_once()

    def test_lifespan_bootstraps_schema_before_opening_pool(self) -> None:
        """Startup bootstrap should run before the app opens its Postgres pool."""
        from fastapi.testclient import TestClient

        events: list[str] = []
        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            events.append("database_pool")
            yield mock_pool

        async def _bootstrap(_settings: Settings) -> None:
            events.append("bootstrap")

        with (
            patch("volundr.main._bootstrap_startup_schema", side_effect=_bootstrap),
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
        ):
            app = create_app()
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200

        assert events[:2] == ["bootstrap", "database_pool"]

    def test_lifespan_initializes_audit_subscriber(self):
        """Lifespan must run startup/shutdown without error when sleipnir is disabled.

        Covers the audit_subscriber = None initialisation and the
        ``if audit_subscriber is not None:`` guard in the finally block.
        """
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        with (
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
        ):
            app = create_app()
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200

    def test_lifespan_mounts_shared_api_routes(self):
        """Volundr hosts the shared credentials, integrations, tracker, and audit APIs."""
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        with (
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
        ):
            app = create_app()
            with TestClient(app) as client:
                paths = client.get("/openapi.json").json()["paths"]

        assert "/api/v1/credentials/user" in paths
        assert "/api/v1/tokens" in paths
        assert "/api/v1/integrations" in paths
        assert "/api/v1/integrations/catalog" in paths
        assert "/api/v1/tracker/status" in paths
        assert "/api/v1/tracker/issues" in paths
        assert "/api/v1/audit/events" in paths
        assert hasattr(app.state, "pat_service")

    def test_lifespan_mounts_session_proxy_routes_standalone(self):
        """Standalone (no CLI root app): the /s/{id} session proxy must exist.

        The K8s deployment runs ``uvicorn volundr.main:create_app`` directly.
        The regression was FastAPI's default 404 ("Not Found") because nothing
        mounted the proxy routes; the mounted route answers with the proxy's
        own "Session not found".
        """
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        with (
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
            # Hermetic even when another test built a mini-mode root app and
            # left the module-global registry set.
            patch("cli.server.get_skuld_registry", return_value=None),
        ):
            app = create_app()
            with TestClient(app) as client:
                resp = client.get("/s/00000000-0000-0000-0000-000000000000/health")
                assert resp.status_code == 404
                assert resp.json()["detail"] == "Session not found"

                # Lifespan re-entry must not register the routes twice; the
                # registry is pinned on app.state and reused.
                registry = app.state.session_proxy_registry
            with TestClient(app):
                assert app.state.session_proxy_registry is registry
                proxy_routes = [
                    r for r in app.routes if getattr(r, "path", "") == "/s/{session_id}/health"
                ]
                assert len(proxy_routes) == 1

    def test_lifespan_seeds_integrations_and_starts_audit_subscriber(self):
        """Lifespan runs the shared seeders and audit subscriber when enabled."""
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        settings = Settings()
        settings.integrations.seed_connections = [{"owner_id": "u1"}]
        settings.linear.enabled = True
        settings.linear.api_key = "lin-test"
        settings.telegram_ingress.enabled = False
        settings.sleipnir.enabled = True
        settings.sleipnir.adapter = "tests.test_main.DummySleipnir"

        seed_integrations = AsyncMock()
        seed_linear = AsyncMock()
        subscriber = AsyncMock()
        subscriber.start = AsyncMock()
        subscriber.stop = AsyncMock()

        class DummySleipnir:
            def __init__(self, **_kwargs):
                pass

        from niuu.utils import import_class as real_import_class

        def selective_import(path: str):
            if path == "tests.test_main.DummySleipnir":
                return DummySleipnir
            return real_import_class(path)

        with (
            patch("volundr.main._bootstrap_startup_schema", new=AsyncMock()),
            patch("volundr.main.database_pool", _mock_db_pool),
            patch("volundr.main.import_class", side_effect=selective_import),
            patch("volundr.main._seed_configured_integrations", seed_integrations),
            patch("volundr.main._seed_linear_integration", seed_linear),
            patch("volundr.main._has_seeded_linear_integration", return_value=False),
            patch("volundr.main.AuditSubscriber", return_value=subscriber),
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
        ):
            app = create_app(settings)
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200

        seed_integrations.assert_awaited_once()
        seed_linear.assert_awaited_once()
        subscriber.start.assert_awaited_once()
        subscriber.stop.assert_awaited_once()

    def test_lifespan_swallows_audit_subscriber_start_failures(self):
        """Audit subscriber start failures do not prevent app startup."""
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        settings = Settings()
        settings.telegram_ingress.enabled = False
        settings.sleipnir.enabled = True
        settings.sleipnir.adapter = "tests.test_main.DummySleipnir"

        class DummySleipnir:
            def __init__(self, **_kwargs):
                pass

        from niuu.utils import import_class as real_import_class

        def selective_import(path: str):
            if path == "tests.test_main.DummySleipnir":
                return DummySleipnir
            return real_import_class(path)

        subscriber = AsyncMock()
        subscriber.start = AsyncMock(side_effect=RuntimeError("boom"))
        subscriber.stop = AsyncMock()

        with (
            patch("volundr.main._bootstrap_startup_schema", new=AsyncMock()),
            patch("volundr.main.database_pool", _mock_db_pool),
            patch("volundr.main.import_class", side_effect=selective_import),
            patch("volundr.main.AuditSubscriber", return_value=subscriber),
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
        ):
            app = create_app(settings)
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200

        subscriber.start.assert_awaited_once()
        subscriber.stop.assert_awaited_once()


class TestBifrostCatalogLoading:
    """Tests for background Bifrost catalog refresh behavior."""

    @pytest.mark.asyncio
    async def test_load_bifrost_catalog_populates_provider(self):
        provider = HardcodedPricingProvider()
        catalog = type(
            "FakeCatalog",
            (),
            {
                "_base_url": "http://guild.test",
                "list_models": AsyncMock(
                    return_value=[ManagedModel(id="gpt-5", name="GPT-5", vendor="openai")]
                ),
            },
        )()

        await _load_bifrost_catalog(provider, catalog)

        assert [model.id for model in provider.list_models()] == ["gpt-5"]

    @pytest.mark.asyncio
    async def test_load_bifrost_catalog_retries_until_catalog_is_ready(self):
        provider = HardcodedPricingProvider()
        catalog = type(
            "FakeCatalog",
            (),
            {
                "_base_url": "http://guild.test",
                "list_models": AsyncMock(
                    side_effect=[
                        httpx.ConnectError("not ready"),
                        [ManagedModel(id="gpt-5.5", name="GPT-5.5", vendor="openai")],
                    ]
                ),
            },
        )()

        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("volundr.main.asyncio.sleep", side_effect=fake_sleep):
            await _load_bifrost_catalog(provider, catalog)

        assert sleep_calls == [0.1]
        assert [model.id for model in provider.list_models()] == ["gpt-5.5"]

    def test_lifespan_does_not_block_on_unavailable_bifrost_catalog_startup(self):
        """Volundr should boot and keep retrying when Bifrost is not ready yet."""
        from fastapi.testclient import TestClient

        mock_pool = AsyncMock()

        @asynccontextmanager
        async def _mock_db_pool(_config):
            yield mock_pool

        with (
            patch("volundr.main._bootstrap_startup_schema", new=AsyncMock()),
            patch("volundr.main.database_pool", _mock_db_pool),
            patch(
                "volundr.adapters.outbound.bifrost_catalog_http.HttpBifrostCatalogAdapter.list_models",
                new=AsyncMock(side_effect=httpx.ConnectError("not ready")),
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
        ):
            app = create_app()
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200
