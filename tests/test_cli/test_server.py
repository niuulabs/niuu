"""Tests for cli.server — Root ASGI server, SkuldPortRegistry, and proxies."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from cli.registry import PluginRegistry
from cli.server import (
    _PLUGIN_API_PREFIXES,
    HOST_PROFILES,
    MountedRouteDomain,
    RootServer,
    SkuldPortRegistry,
    _PrefixRestoreApp,
    available_route_domains,
    build_root_app,
    collect_route_inventory,
    get_skuld_registry,
    parse_enabled_mounts,
    resolve_enabled_mounts,
)
from niuu.ports.embedded_database import ConnectionInfo
from niuu.ports.plugin import APIRouteDomain
from tests.test_cli.conftest import FakePlugin


class TestSkuldPortRegistry:
    """Tests for SkuldPortRegistry register/unregister/get_port."""

    def test_register_and_get_port(self) -> None:
        reg = SkuldPortRegistry()
        reg.register("sess-1", 9100)
        assert reg.get_port("sess-1") == 9100

    def test_unregister(self) -> None:
        reg = SkuldPortRegistry()
        reg.register("sess-1", 9100)
        reg.unregister("sess-1")
        assert reg.get_port("sess-1") is None

    def test_unregister_nonexistent(self) -> None:
        reg = SkuldPortRegistry()
        reg.unregister("does-not-exist")  # should not raise

    def test_get_port_unknown(self) -> None:
        reg = SkuldPortRegistry()
        assert reg.get_port("unknown") is None

    def test_register_overwrites(self) -> None:
        reg = SkuldPortRegistry()
        reg.register("sess-1", 9100)
        reg.register("sess-1", 9200)
        assert reg.get_port("sess-1") == 9200

    def test_get_port_recovers_from_persisted_state(self, tmp_path: Path) -> None:
        state_file = tmp_path / "forge-state.json"
        state_file.write_text(
            '{"sess-1":{"session_id":"sess-1","port":9100,"state":"running"}}',
            encoding="utf-8",
        )
        reg = SkuldPortRegistry(state_file=state_file)
        assert reg.get_port("sess-1") == 9100


class TestGetSkuldRegistry:
    """Tests for the module-level get_skuld_registry function."""

    def test_returns_registry_after_rootserver_init(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        result = get_skuld_registry()
        assert result is server.skuld_registry
        assert isinstance(result, SkuldPortRegistry)


class TestPrefixRestoreApp:
    """Tests for _PrefixRestoreApp ASGI wrapper."""

    @pytest.mark.asyncio
    async def test_restores_http_path_prefix(self) -> None:
        received_scope: dict = {}

        async def fake_app(scope, receive, send):
            received_scope.update(scope)

        wrapper = _PrefixRestoreApp(fake_app, "/api/v1/volundr")
        scope = {
            "type": "http",
            "path": "/sessions",
            "raw_path": b"/sessions",
        }
        await wrapper(scope, AsyncMock(), AsyncMock())
        assert received_scope["path"] == "/api/v1/volundr/sessions"
        assert received_scope["raw_path"] == b"/api/v1/volundr/sessions"

    @pytest.mark.asyncio
    async def test_restores_websocket_path_prefix(self) -> None:
        received_scope: dict = {}

        async def fake_app(scope, receive, send):
            received_scope.update(scope)

        wrapper = _PrefixRestoreApp(fake_app, "/api/v1/tyr")
        scope = {
            "type": "websocket",
            "path": "/ws",
            "raw_path": b"/ws",
        }
        await wrapper(scope, AsyncMock(), AsyncMock())
        assert received_scope["path"] == "/api/v1/tyr/ws"

    @pytest.mark.asyncio
    async def test_passes_lifespan_unchanged(self) -> None:
        received_scope: dict = {}

        async def fake_app(scope, receive, send):
            received_scope.update(scope)

        wrapper = _PrefixRestoreApp(fake_app, "/api/v1/volundr")
        scope = {"type": "lifespan", "path": "/something"}
        await wrapper(scope, AsyncMock(), AsyncMock())
        # lifespan should not be modified
        assert received_scope["path"] == "/something"

    @pytest.mark.asyncio
    async def test_handles_missing_raw_path(self) -> None:
        received_scope: dict = {}

        async def fake_app(scope, receive, send):
            received_scope.update(scope)

        wrapper = _PrefixRestoreApp(fake_app, "/api/v1/volundr")
        scope = {"type": "http", "path": "/sessions"}
        await wrapper(scope, AsyncMock(), AsyncMock())
        assert received_scope["path"] == "/api/v1/volundr/sessions"
        assert "raw_path" not in received_scope or received_scope.get("raw_path") is None


class TestRootServerInit:
    """Tests for RootServer constructor."""

    def test_default_host_and_port(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        assert server._host == "127.0.0.1"
        assert server._port == 8080

    def test_custom_host_and_port(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry, host="0.0.0.0", port=9090)
        assert server._host == "0.0.0.0"
        assert server._port == 9090

    def test_skuld_registry_initialized(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        assert isinstance(server.skuld_registry, SkuldPortRegistry)

    def test_accepts_host_profile_and_mounts(self) -> None:
        registry = PluginRegistry()
        server = RootServer(
            registry=registry,
            host_profile="api",
            enabled_mounts={"niuu-api", "runtime-config"},
        )
        assert server._host_profile == "api"
        assert server._enabled_mounts == {"niuu-api", "runtime-config"}


class TestRouteDomainSelection:
    def test_available_route_domains_lists_known_domains(self) -> None:
        assert available_route_domains() == {
            "admin-api",
            "audit-api",
            "bifrost-api",
            "bifrost-observability-api",
            "catalog-legacy-api",
            "credentials-api",
            "credentials-legacy-api",
            "features-api",
            "features-legacy-api",
            "forge-api",
            "forge-legacy-api",
            "git-api",
            "git-legacy-api",
            "identity-api",
            "identity-legacy-api",
            "integrations-api",
            "integrations-legacy-api",
            "llm-api",
            "mimir-api",
            "niuu-api",
            "observatory-api",
            "observatory-events-api",
            "observatory-registry-api",
            "observatory-topology-api",
            "persona-api",
            "ravn-api",
            "ravn-budget-api",
            "ravn-runtime-api",
            "ravn-session-api",
            "ravn-trigger-api",
            "catalog-api",
            "dispatch-api",
            "event-api",
            "review-api",
            "session-api",
            "session-legacy-api",
            "saga-api",
            "settings-api",
            "tenancy-api",
            "tracker-api",
            "tokens-api",
            "tokens-legacy-api",
            "volundr-api",
            "workflow-api",
            "workspace-api",
            "workspace-legacy-api",
            "tyr-api",
            "skuld-proxy",
            "runtime-config",
            "web-ui",
        }

    def test_host_profiles_cover_full_and_api(self) -> None:
        assert "full" in HOST_PROFILES
        assert "api" in HOST_PROFILES
        assert "full-compat" in HOST_PROFILES
        assert "api-compat" in HOST_PROFILES
        assert "web-ui" in HOST_PROFILES["full"]
        assert "web-ui" not in HOST_PROFILES["api"]
        assert "volundr-api" not in HOST_PROFILES["full"]
        assert "volundr-api" in HOST_PROFILES["full-compat"]

    def test_parse_enabled_mounts_empty_returns_none(self) -> None:
        assert parse_enabled_mounts("") is None

    def test_parse_enabled_mounts_parses_csv(self) -> None:
        mounts = parse_enabled_mounts("niuu-api, runtime-config")
        assert mounts == {"niuu-api", "runtime-config"}

    def test_parse_enabled_mounts_rejects_unknown_domains(self) -> None:
        with pytest.raises(ValueError, match="Unknown route domains"):
            parse_enabled_mounts("unknown-domain")

    def test_resolve_enabled_mounts_uses_profile_defaults(self) -> None:
        mounts = resolve_enabled_mounts("api")
        assert mounts == HOST_PROFILES["api"]

    def test_resolve_enabled_mounts_no_web_overrides_profile(self) -> None:
        mounts = resolve_enabled_mounts("full", no_web=True)
        assert "web-ui" not in mounts

    def test_resolve_enabled_mounts_rejects_unknown_profile(self) -> None:
        with pytest.raises(ValueError, match="Unknown host profile"):
            resolve_enabled_mounts("unknown")

    def test_collect_route_inventory_includes_internal_and_plugin_domains(self) -> None:
        class NiuuPlugin(FakePlugin):
            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="niuu-api",
                        prefixes=("/api/v1/niuu",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(NiuuPlugin(name="niuu"))
        inventory = collect_route_inventory(
            registry=registry,
            enabled_mounts={"niuu-api", "runtime-config"},
        )
        assert inventory == (
            MountedRouteDomain(
                name="niuu-api",
                prefixes=("/api/v1/niuu",),
                source="plugin",
                plugin_name="niuu",
            ),
            MountedRouteDomain(
                name="runtime-config",
                prefixes=("/config.json",),
                source="internal",
                plugin_name=None,
            ),
        )

    def test_collect_route_inventory_merges_shared_domain_prefixes(self) -> None:
        class VolundrPlugin(FakePlugin):
            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="tracker-api",
                        prefixes=("/api/v1/tracker/issues",),
                    ),
                )

        class TyrPlugin(FakePlugin):
            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="tracker-api",
                        prefixes=("/api/v1/tracker/projects",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))
        registry.register(TyrPlugin(name="tyr"))
        inventory = collect_route_inventory(registry=registry, enabled_mounts={"tracker-api"})
        assert inventory == (
            MountedRouteDomain(
                name="tracker-api",
                prefixes=("/api/v1/tracker/projects", "/api/v1/tracker/issues"),
                source="plugin",
                plugin_name="tyr,volundr",
            ),
        )


class TestRootServerBuildApp:
    """Tests for RootServer._build_app()."""

    def test_build_app_returns_fastapi(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        assert isinstance(app, FastAPI)

    def test_health_endpoint(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_cors_headers_are_only_added_when_configured(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(
            os.environ,
            {
                "NIUU_NO_WEB": "true",
                "NIUU_CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
            },
        ):
            app = server._build_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_config_json_endpoint(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry, host="127.0.0.1", port=8080)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)
        resp = client.get("/config.json")
        assert resp.status_code == 200
        assert resp.json()["apiBaseUrl"] == "http://127.0.0.1:8080"

    def test_skuld_http_proxy_session_not_found(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)
        resp = client.get("/s/nonexistent/api/some/path")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_skuld_health_proxy_session_not_found(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)
        resp = client.get("/s/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_skuld_http_proxy_forwards_request(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        server.skuld_registry.register("sess-1", 9100)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.content = b'{"ok": true}'
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-1/api/data")

        assert resp.status_code == 200

    def test_skuld_http_proxy_connect_error(self) -> None:
        import httpx

        registry = PluginRegistry()
        server = RootServer(registry=registry)
        server.skuld_registry.register("sess-1", 9100)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-1/api/data")

        assert resp.status_code == 502
        assert "not ready" in resp.json()["detail"].lower()

    def test_skuld_health_proxy_forwards_when_registered(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        server.skuld_registry.register("sess-1", 9100)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.content = b'{"status": "ok"}'
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-1/health")

        assert resp.status_code == 200

    def test_skuld_health_proxy_connect_error(self) -> None:
        import httpx

        registry = PluginRegistry()
        server = RootServer(registry=registry)
        server.skuld_registry.register("sess-1", 9100)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-1/health")

        assert resp.status_code == 502

    def test_no_web_skips_static_files(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app, raise_server_exceptions=False)
        # Without web UI, there should be no SPA fallback
        resp = client.get("/some-random-page")
        assert resp.status_code in (404, 405)

    def test_web_ui_with_dist_directory(self, tmp_path: Path) -> None:
        """When web dist dir exists, SPA fallback is mounted."""
        dist = tmp_path / "dist"
        dist.mkdir()
        assets = dist / "assets"
        assets.mkdir()
        (assets / "main.js").write_text("// js")
        (dist / "index.html").write_text("<html>SPA</html>")
        (dist / "config.live.json").write_text(
            '{"services":{"forge":{"mode":"http","baseUrl":"http://localhost:8080/api/v1/forge"}}}'
        )
        favicon = dist / "favicon.svg"
        favicon.write_text("<svg/>")

        registry = PluginRegistry()
        server = RootServer(registry=registry)

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("cli.resources.web_dist_dir", return_value=dist),
        ):
            # Remove NIUU_NO_WEB if set
            os.environ.pop("NIUU_NO_WEB", None)
            app = server._build_app()

        client = TestClient(app)
        # SPA fallback should serve index.html for unknown routes
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"SPA" in resp.content

        config_resp = client.get("/config.live.json")
        assert config_resp.status_code == 200
        assert config_resp.headers["content-type"].startswith("application/json")
        assert config_resp.json()["services"]["forge"]["mode"] == "http"
        assert config_resp.json()["services"]["forge"]["baseUrl"] == "http://testserver/api/v1/forge"

        default_config_resp = client.get("/config.json")
        assert default_config_resp.status_code == 200
        assert default_config_resp.headers["content-type"].startswith("application/json")
        assert default_config_resp.json()["services"]["forge"]["mode"] == "http"
        assert (
            default_config_resp.json()["services"]["forge"]["baseUrl"]
            == "http://testserver/api/v1/forge"
        )

    def test_web_ui_filenotfound_gracefully_handled(self) -> None:
        """When web_dist_dir raises FileNotFoundError, app is still returned."""
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("cli.resources.web_dist_dir", side_effect=FileNotFoundError("no dist")),
        ):
            os.environ.pop("NIUU_NO_WEB", None)
            app = server._build_app()

        assert isinstance(app, FastAPI)

    def test_mounts_plugin_sub_apps(self) -> None:
        """Plugin sub-apps are mounted at their API prefixes."""
        sub_app = FastAPI()

        @sub_app.get("/api/v1/volundr/ping")
        async def ping():
            return {"pong": True}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return sub_app

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        server = RootServer(registry=registry, enabled_mounts={"volundr-api"})
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()

        client = TestClient(app)
        resp = client.get("/api/v1/volundr/ping")
        assert resp.status_code == 200
        assert resp.json() == {"pong": True}

    def test_build_root_app_with_explicit_mounts_only_exposes_selected_routes(self) -> None:
        niuu_app = FastAPI()

        @niuu_app.get("/api/v1/niuu/ping")
        async def ping():
            return {"pong": "niuu"}

        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/volundr/ping")
        async def volundr_ping():
            return {"pong": "volundr"}

        class NiuuPlugin(FakePlugin):
            def create_api_app(self):
                return niuu_app

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

        registry = PluginRegistry()
        registry.register(NiuuPlugin(name="niuu"))
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"niuu-api", "runtime-config"},
        )

        client = TestClient(app)
        assert app.state.route_inventory == (
            MountedRouteDomain(
                name="niuu-api",
                prefixes=("/api/v1/niuu",),
                source="plugin",
                plugin_name="niuu",
            ),
            MountedRouteDomain(
                name="runtime-config",
                prefixes=("/config.json",),
                source="internal",
                plugin_name=None,
            ),
        )
        assert client.get("/api/v1/niuu/ping").status_code == 200
        assert client.get("/api/v1/volundr/ping").status_code == 404

    def test_build_root_app_exposes_legacy_route_usage_when_niuu_api_is_mounted(self) -> None:
        registry = PluginRegistry()
        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"niuu-api"},
        )
        app.state.legacy_route_hits = {
            ("/api/v1/volundr/me", "/api/v1/identity/me", "GET"): 3,
            ("/api/v1/volundr/users", "/api/v1/volundr/admin/users", "GET"): 1,
        }

        client = TestClient(app)
        response = client.get("/api/v1/niuu/compat/legacy-routes")

        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "legacyPath": "/api/v1/volundr/me",
                    "canonicalPath": "/api/v1/identity/me",
                    "method": "GET",
                    "hits": 3,
                },
                {
                    "legacyPath": "/api/v1/volundr/users",
                    "canonicalPath": "/api/v1/volundr/admin/users",
                    "method": "GET",
                    "hits": 1,
                },
            ],
            "totalHits": 4,
        }

    def test_build_root_app_can_clear_legacy_route_usage_when_niuu_api_is_mounted(self) -> None:
        registry = PluginRegistry()
        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"niuu-api"},
        )
        app.state.legacy_route_hits = {
            ("/api/v1/volundr/me", "/api/v1/identity/me", "GET"): 2,
        }

        client = TestClient(app)
        response = client.delete("/api/v1/niuu/compat/legacy-routes")

        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "legacyPath": "/api/v1/volundr/me",
                    "canonicalPath": "/api/v1/identity/me",
                    "method": "GET",
                    "hits": 2,
                }
            ],
            "totalHits": 2,
            "cleared": True,
        }
        assert app.state.legacy_route_hits == {}

    def test_build_root_app_hides_legacy_route_usage_when_niuu_api_not_mounted(self) -> None:
        registry = PluginRegistry()
        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"runtime-config"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/niuu/compat/legacy-routes").status_code == 404
        assert client.delete("/api/v1/niuu/compat/legacy-routes").status_code == 404

    def test_build_root_app_mounts_shared_tracker_domain_across_plugins(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/tracker/issues")
        async def tracker_issues():
            return [{"id": "issue-1"}]

        @volundr_app.get("/api/v1/volundr/ping")
        async def volundr_ping():
            return {"pong": "volundr"}

        tyr_app = FastAPI()

        @tyr_app.get("/api/v1/tracker/projects")
        async def tracker_projects():
            return [{"id": "proj-1"}]

        @tyr_app.get("/api/v1/tyr/ping")
        async def tyr_ping():
            return {"pong": "tyr"}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="tracker-api",
                        prefixes=("/api/v1/tracker/issues",),
                    ),
                )

        class TyrPlugin(FakePlugin):
            def create_api_app(self):
                return tyr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="tracker-api",
                        prefixes=("/api/v1/tracker/projects",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))
        registry.register(TyrPlugin(name="tyr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"tracker-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/tracker/issues").status_code == 200
        assert client.get("/api/v1/tracker/projects").status_code == 200
        assert client.get("/api/v1/volundr/ping").status_code == 404
        assert client.get("/api/v1/tyr/ping").status_code == 404
        assert client.get("/config.json").status_code == 404
        assert client.get("/s/example/health").status_code == 404

    def test_build_root_app_mounts_shared_audit_domain_across_plugins(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/audit")
        async def canonical_audit():
            return [{"id": "volundr-canonical"}]

        @volundr_app.get("/audit")
        async def legacy_audit():
            return [{"id": "volundr-legacy"}]

        tyr_app = FastAPI()

        @tyr_app.get("/api/v1/tyr/audit")
        async def tyr_audit():
            return [{"id": "tyr-audit"}]

        @tyr_app.get("/api/v1/tyr/sagas")
        async def tyr_sagas():
            return [{"id": "saga-1"}]

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="audit-api",
                        prefixes=("/api/v1/audit", "/audit"),
                    ),
                )

        class TyrPlugin(FakePlugin):
            def create_api_app(self):
                return tyr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="saga-api",
                        prefixes=("/api/v1/tyr/sagas",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))
        registry.register(TyrPlugin(name="tyr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"audit-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/audit").status_code == 200
        assert client.get("/audit").status_code == 200
        assert client.get("/api/v1/tyr/audit").status_code == 404
        assert client.get("/api/v1/tyr/sagas").status_code == 404

    def test_build_root_app_can_mount_admin_slice_without_tenancy(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/volundr/admin/ping")
        async def admin_ping():
            return {"pong": "admin"}

        @volundr_app.get("/api/v1/volundr/tenants/ping")
        async def tenant_ping():
            return {"pong": "tenant"}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="admin-api",
                        prefixes=("/api/v1/volundr/admin",),
                    ),
                    APIRouteDomain(
                        name="tenancy-api",
                        prefixes=("/api/v1/volundr/tenants",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"admin-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/volundr/admin/ping").status_code == 200
        assert client.get("/api/v1/volundr/tenants/ping").status_code == 404

    def test_build_root_app_can_mount_session_slice_without_workspace_slice(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/volundr/sessions/ping")
        async def session_ping():
            return {"pong": "session"}

        @volundr_app.get("/api/v1/volundr/chronicles/ping")
        async def chronicle_ping():
            return {"pong": "chronicle"}

        @volundr_app.get("/api/v1/volundr/events/ping")
        async def events_ping():
            return {"pong": "events"}

        @volundr_app.get("/api/v1/volundr/workspaces/ping")
        async def workspace_ping():
            return {"pong": "workspace"}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="session-api",
                        prefixes=(
                            "/api/v1/forge/sessions",
                            "/api/v1/forge/chronicles",
                            "/api/v1/forge/events",
                            "/api/v1/volundr/sessions",
                            "/api/v1/volundr/chronicles",
                            "/api/v1/volundr/events",
                        ),
                    ),
                    APIRouteDomain(
                        name="workspace-api",
                        prefixes=("/api/v1/forge/workspaces", "/api/v1/volundr/workspaces"),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"session-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/volundr/sessions/ping").status_code == 200
        assert client.get("/api/v1/forge/sessions/ping").status_code == 200
        assert client.get("/api/v1/volundr/chronicles/ping").status_code == 200
        assert client.get("/api/v1/forge/chronicles/ping").status_code == 200
        assert client.get("/api/v1/volundr/events/ping").status_code == 200
        assert client.get("/api/v1/forge/events/ping").status_code == 200
        assert client.get("/api/v1/volundr/workspaces/ping").status_code == 404
        assert client.get("/api/v1/forge/workspaces/ping").status_code == 404

    def test_build_root_app_can_mount_forge_slice_with_canonical_aliases(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/volundr/templates/ping")
        async def template_ping():
            return {"pong": "template"}

        @volundr_app.get("/api/v1/volundr/stats")
        async def stats():
            return {"sessions": 3}

        @volundr_app.get("/api/v1/volundr/models")
        async def models():
            return {"claude-sonnet-4-6": {"name": "Claude Sonnet 4.6"}}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="forge-api",
                        prefixes=(
                            "/api/v1/forge/templates",
                            "/api/v1/forge/stats",
                            "/api/v1/forge/models",
                            "/api/v1/volundr/templates",
                            "/api/v1/volundr/stats",
                            "/api/v1/volundr/models",
                        ),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"forge-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/forge/templates/ping").status_code == 200
        assert client.get("/api/v1/forge/stats").json() == {"sessions": 3}
        assert client.get("/api/v1/forge/models").status_code == 200
        assert client.get("/api/v1/volundr/templates/ping").status_code == 200

    def test_build_root_app_can_mount_saga_slice_without_dispatch_slice(self) -> None:
        tyr_app = FastAPI()

        @tyr_app.get("/api/v1/tyr/sagas/ping")
        async def saga_ping():
            return {"pong": "saga"}

        @tyr_app.get("/api/v1/tyr/dispatch/ping")
        async def dispatch_ping():
            return {"pong": "dispatch"}

        class TyrPlugin(FakePlugin):
            def create_api_app(self):
                return tyr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="saga-api",
                        prefixes=("/api/v1/tyr/sagas",),
                    ),
                    APIRouteDomain(
                        name="dispatch-api",
                        prefixes=("/api/v1/tyr/dispatch",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(TyrPlugin(name="tyr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"saga-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/tyr/sagas/ping").status_code == 200
        assert client.get("/api/v1/tyr/dispatch/ping").status_code == 404

    def test_build_root_app_can_mount_review_slice_without_saga_slice(self) -> None:
        tyr_app = FastAPI()

        @tyr_app.get("/api/v1/tyr/raids/ping")
        async def raid_ping():
            return {"pong": "review"}

        @tyr_app.get("/api/v1/tyr/sagas/ping")
        async def saga_ping():
            return {"pong": "saga"}

        class TyrPlugin(FakePlugin):
            def create_api_app(self):
                return tyr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="review-api",
                        prefixes=("/api/v1/tyr/raids",),
                    ),
                    APIRouteDomain(
                        name="saga-api",
                        prefixes=("/api/v1/tyr/sagas",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(TyrPlugin(name="tyr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"review-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/tyr/raids/ping").status_code == 200
        assert client.get("/api/v1/tyr/sagas/ping").status_code == 404

    def test_build_root_app_can_mount_credentials_slice_with_legacy_shims(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/credentials/user")
        async def canonical_credentials():
            return {"route": "canonical-credentials"}

        @volundr_app.get("/api/v1/volundr/credentials")
        async def legacy_credentials():
            return {"route": "legacy-credentials"}

        @volundr_app.get("/api/v1/volundr/secrets")
        async def legacy_secrets():
            return {"route": "legacy-secrets"}

        @volundr_app.get("/api/v1/volundr/secrets/store")
        async def legacy_secret_store():
            return {"route": "legacy-secret-store"}

        @volundr_app.get("/api/v1/volundr/secrets/types")
        async def legacy_secret_types():
            return {"route": "legacy-secret-types"}

        @volundr_app.get("/api/v1/volundr/sessions")
        async def unrelated_sessions():
            return {"route": "sessions"}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="credentials-api",
                        prefixes=(
                            "/api/v1/credentials",
                            "/api/v1/volundr/credentials",
                            "/api/v1/volundr/secrets",
                        ),
                    ),
                    APIRouteDomain(
                        name="session-api",
                        prefixes=("/api/v1/volundr/sessions",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"credentials-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/credentials/user").status_code == 200
        assert client.get("/api/v1/volundr/credentials").status_code == 200
        assert client.get("/api/v1/volundr/secrets").status_code == 200
        assert client.get("/api/v1/volundr/secrets/store").status_code == 200
        assert client.get("/api/v1/volundr/secrets/types").status_code == 200
        assert client.get("/api/v1/volundr/sessions").status_code == 404

    def test_build_root_app_can_mount_integrations_slice_with_legacy_shims(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/integrations")
        async def canonical_integrations():
            return {"route": "canonical-integrations"}

        @volundr_app.get("/api/v1/volundr/integrations")
        async def legacy_integrations():
            return {"route": "legacy-integrations"}

        @volundr_app.get("/api/v1/volundr/integrations/oauth/linear/start")
        async def legacy_oauth_start():
            return {"route": "legacy-oauth-start"}

        @volundr_app.get("/api/v1/volundr/repos")
        async def unrelated_repos():
            return {"route": "repos"}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="integrations-api",
                        prefixes=(
                            "/api/v1/integrations",
                            "/api/v1/volundr/integrations",
                        ),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"integrations-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/integrations").status_code == 200
        assert client.get("/api/v1/volundr/integrations").status_code == 200
        assert client.get("/api/v1/volundr/integrations/oauth/linear/start").status_code == 200
        assert client.get("/api/v1/volundr/repos").status_code == 404

    def test_build_root_app_can_mount_tyr_settings_without_workflow_routes(self) -> None:
        tyr_app = FastAPI()

        @tyr_app.get("/api/v1/tyr/settings/flock")
        async def flock_settings():
            return {"route": "settings"}

        @tyr_app.get("/api/v1/tyr/flock")
        async def flock_workflow():
            return {"route": "workflow"}

        class TyrPlugin(FakePlugin):
            def create_api_app(self):
                return tyr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="settings-api",
                        prefixes=("/api/v1/tyr/settings",),
                    ),
                    APIRouteDomain(
                        name="workflow-api",
                        prefixes=(
                            "/api/v1/tyr/flock",
                            "/api/v1/tyr/flock_flows",
                            "/api/v1/tyr/pipelines",
                        ),
                    ),
                )

        registry = PluginRegistry()
        registry.register(TyrPlugin(name="tyr"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"settings-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/tyr/settings/flock").status_code == 200
        assert client.get("/api/v1/tyr/flock").status_code == 404

    def test_build_root_app_mounts_canonical_mimir_prefixes_via_backend_translation(self) -> None:
        mimir_app = FastAPI()

        @mimir_app.get("/mimir/stats")
        async def mimir_stats():
            return {"pages": 12}

        @mimir_app.get("/mcp/health")
        async def mimir_mcp_health():
            return {"status": "ok"}

        class MimirPlugin(FakePlugin):
            def create_api_app(self):
                return mimir_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="mimir-api",
                        prefixes=(
                            "/api/v1/mimir/mcp",
                            "/api/v1/mimir",
                            "/mcp",
                            "/mimir",
                        ),
                    ),
                )

        registry = PluginRegistry()
        registry.register(MimirPlugin(name="mimir"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"mimir-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/mimir/stats").json() == {"pages": 12}
        assert client.get("/api/v1/mimir/mcp/health").json() == {"status": "ok"}
        assert client.get("/mimir/stats").json() == {"pages": 12}
        assert client.get("/mcp/health").json() == {"status": "ok"}

    def test_build_root_app_mounts_canonical_bifrost_prefixes_via_backend_translation(self) -> None:
        bifrost_app = FastAPI()

        @bifrost_app.get("/v1/models")
        async def list_models():
            return {"data": []}

        @bifrost_app.get("/metrics")
        async def metrics():
            return {"metrics": "ok"}

        @bifrost_app.get("/healthz")
        async def healthz():
            return {"status": "ok"}

        class BifrostPlugin(FakePlugin):
            def create_api_app(self):
                return bifrost_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="llm-api",
                        prefixes=(
                            "/api/v1/bifrost/health",
                            "/api/v1/bifrost/admin",
                            "/api/v1/bifrost/v1",
                            "/api/v1/bifrost/api",
                        ),
                    ),
                    APIRouteDomain(
                        name="bifrost-observability-api",
                        prefixes=(
                            "/api/v1/bifrost/metrics",
                            "/api/v1/bifrost/healthz",
                            "/api/v1/bifrost/readyz",
                        ),
                    ),
                )

        registry = PluginRegistry()
        registry.register(BifrostPlugin(name="bifrost"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"llm-api", "bifrost-observability-api"},
        )

        client = TestClient(app)
        assert client.get("/api/v1/bifrost/v1/models").json() == {"data": []}
        assert client.get("/api/v1/bifrost/metrics").json() == {"metrics": "ok"}
        assert client.get("/api/v1/bifrost/healthz").json() == {"status": "ok"}
        assert client.get("/v1/models").status_code == 404
        assert client.get("/metrics").status_code == 404

    def test_build_root_app_merges_mounted_plugin_routes_into_openapi(self) -> None:
        volundr_app = FastAPI()

        @volundr_app.get("/api/v1/volundr/sessions")
        async def list_sessions():
            return [{"id": "sess-1"}]

        mimir_app = FastAPI()

        @mimir_app.get("/mimir/stats")
        async def mimir_stats():
            return {"healthy": True}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return volundr_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="forge-api",
                        prefixes=("/api/v1/forge/sessions",),
                    ),
                    APIRouteDomain(
                        name="forge-legacy-api",
                        prefixes=("/api/v1/volundr/sessions",),
                    ),
                )

        class MimirPlugin(FakePlugin):
            def create_api_app(self):
                return mimir_app

            def api_route_domains(self):
                return (
                    APIRouteDomain(
                        name="mimir-api",
                        prefixes=("/api/v1/mimir",),
                    ),
                )

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))
        registry.register(MimirPlugin(name="mimir"))

        app = build_root_app(
            registry=registry,
            host="127.0.0.1",
            port=8080,
            enabled_mounts={"forge-api", "mimir-api"},
        )

        client = TestClient(app)
        response = client.get("/openapi.json")

        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/health" in paths
        assert "/api/v1/forge/sessions" in paths
        assert "/api/v1/volundr/sessions" not in paths
        assert "/api/v1/mimir/stats" in paths

    def test_plugin_create_api_app_returns_none(self) -> None:
        """Plugin that returns None from create_api_app is skipped."""
        registry = PluginRegistry()
        registry.register(FakePlugin(name="volundr"))

        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()

        assert isinstance(app, FastAPI)

    def test_plugin_create_api_app_exception(self) -> None:
        """Plugin that raises in create_api_app is skipped gracefully."""

        class BrokenPlugin(FakePlugin):
            def create_api_app(self):
                raise RuntimeError("Broken")

        registry = PluginRegistry()
        registry.register(BrokenPlugin(name="volundr"))

        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()

        assert isinstance(app, FastAPI)


class TestRootServerStartStop:
    """Tests for RootServer.start() and stop()."""

    @pytest.mark.asyncio
    async def test_start_calls_embedded_db_and_build_app(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        # Mock the internal methods
        server._start_embedded_db = AsyncMock()
        server._run_migrations = AsyncMock()

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = AsyncMock()

        with (
            patch.dict(os.environ, {"NIUU_NO_WEB": "true"}),
            patch("uvicorn.Server", return_value=mock_uvicorn_server),
            patch("uvicorn.Config"),
        ):
            await server.start()
            assert os.environ["NIUU_SERVER_HOST"] == "127.0.0.1"
            assert os.environ["NIUU_SERVER_PORT"] == "8080"
            assert os.environ["VOLUNDR__URL"] == "http://127.0.0.1:8080"

        server._start_embedded_db.assert_awaited_once()
        server._run_migrations.assert_awaited_once()
        assert server._server is mock_uvicorn_server

    @pytest.mark.asyncio
    async def test_start_sets_loopback_safe_volundr_url(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry, host="0.0.0.0", port=18080)

        server._start_embedded_db = AsyncMock()
        server._run_migrations = AsyncMock()

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = AsyncMock()

        with (
            patch.dict(os.environ, {"NIUU_NO_WEB": "true"}, clear=False),
            patch("uvicorn.Server", return_value=mock_uvicorn_server),
            patch("uvicorn.Config"),
        ):
            await server.start()
            assert os.environ["NIUU_SERVER_HOST"] == "0.0.0.0"
            assert os.environ["NIUU_SERVER_PORT"] == "18080"
            assert os.environ["VOLUNDR__URL"] == "http://127.0.0.1:18080"

    @pytest.mark.asyncio
    async def test_stop_shuts_down_server_and_db(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        mock_uvicorn_server = MagicMock()
        mock_db = AsyncMock()

        # Create a real completed task
        async def _noop():
            pass

        loop = asyncio.get_event_loop()
        mock_task = loop.create_task(_noop())
        await mock_task  # let it complete

        server._server = mock_uvicorn_server
        server._task = mock_task
        server._embedded_db = mock_db

        await server.stop()

        assert mock_uvicorn_server.should_exit is True
        mock_db.stop.assert_awaited_once()
        assert server._task is None
        assert server._embedded_db is None

    @pytest.mark.asyncio
    async def test_stop_no_server(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        # Should not raise when nothing is running
        await server.stop()

    @pytest.mark.asyncio
    async def test_health_check_false_when_no_server(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        assert await server.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_true_when_started(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        mock_server = MagicMock()
        mock_server.started = True
        server._server = mock_server
        assert await server.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false_when_not_started(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        mock_server = MagicMock()
        mock_server.started = False
        server._server = mock_server
        assert await server.health_check() is False


class TestRootServerRunMigrations:
    """Tests for RootServer._run_migrations()."""

    @pytest.mark.asyncio
    async def test_skips_when_no_embedded_db(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        server._embedded_db = None
        # Should not raise
        await server._run_migrations()

    @pytest.mark.asyncio
    async def test_applies_migrations(self, tmp_path: Path) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        mock_db = MagicMock()
        mock_db._connection_info = ConnectionInfo(
            host="127.0.0.1", port=5432, dbname="niuu", user="postgres"
        )
        server._embedded_db = mock_db

        # Create mock migration files
        vol_dir = tmp_path / "volundr"
        vol_dir.mkdir()
        (vol_dir / "000001_init.up.sql").write_text("CREATE TABLE IF NOT EXISTS t1 (id INT);")

        tyr_dir = tmp_path / "tyr"
        tyr_dir.mkdir()
        (tyr_dir / "000001_init.up.sql").write_text("CREATE TABLE IF NOT EXISTS t2 (id INT);")

        def mock_migration_dir(variant):
            if variant == "volundr":
                return vol_dir
            if variant == "tyr":
                return tyr_dir
            raise FileNotFoundError

        mock_conn = AsyncMock()

        with (
            patch("asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn),
            patch("cli.resources.migration_dir", side_effect=mock_migration_dir),
        ):
            await server._run_migrations()

        assert mock_conn.execute.await_count == 2
        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_migration_errors_gracefully(self, tmp_path: Path) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        mock_db = MagicMock()
        mock_db._connection_info = ConnectionInfo(
            host="127.0.0.1", port=5432, dbname="niuu", user="postgres"
        )
        server._embedded_db = mock_db

        vol_dir = tmp_path / "volundr"
        vol_dir.mkdir()
        (vol_dir / "000001_init.up.sql").write_text("INVALID SQL;")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("syntax error"))

        with (
            patch("asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn),
            patch("cli.resources.migration_dir", side_effect=[vol_dir, FileNotFoundError]),
        ):
            # Should not raise
            await server._run_migrations()

    @pytest.mark.asyncio
    async def test_handles_connect_failure_gracefully(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        mock_db = MagicMock()
        mock_db._connection_info = ConnectionInfo(
            host="127.0.0.1", port=5432, dbname="niuu", user="postgres"
        )
        server._embedded_db = mock_db

        with patch("asyncpg.connect", new_callable=AsyncMock, side_effect=Exception("fail")):
            # Should not raise
            await server._run_migrations()


class TestRootServerStartEmbeddedDb:
    """Tests for RootServer._start_embedded_db()."""

    @pytest.mark.asyncio
    async def test_starts_and_sets_env_vars(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)

        mock_db = AsyncMock()
        mock_db.start = AsyncMock(
            return_value=ConnectionInfo(
                host="localhost", port=5433, dbname="testdb", user="testuser"
            )
        )

        with patch(
            "niuu.adapters.embedded_postgres.EmbeddedPostgresDatabase",
            return_value=mock_db,
        ):
            await server._start_embedded_db()

        assert server._embedded_db is mock_db
        assert os.environ["DATABASE__HOST"] == "localhost"
        assert os.environ["DATABASE__PORT"] == "5433"
        assert os.environ["DATABASE__USER"] == "testuser"
        assert os.environ["DATABASE__NAME"] == "testdb"


class TestRootServerLifespan:
    """Tests for the root app lifespan (sub-app startup/shutdown)."""

    def test_lifespan_starts_and_stops_sub_apps(self) -> None:
        """Sub-app lifespans are started on enter and stopped on exit."""
        from contextlib import asynccontextmanager

        started = []
        stopped = []

        @asynccontextmanager
        async def sub_lifespan(app):
            started.append("volundr")
            yield
            stopped.append("volundr")

        sub_app = FastAPI(lifespan=sub_lifespan)

        @sub_app.get("/api/v1/volundr/ping")
        async def ping():
            return {"pong": True}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return sub_app

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        server = RootServer(registry=registry, enabled_mounts={"volundr-api"})
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()

        # TestClient with context manager triggers lifespan
        with TestClient(app) as client:
            assert "volundr" in started
            resp = client.get("/health")
            assert resp.status_code == 200

        assert "volundr" in stopped

    def test_lifespan_handles_sub_app_start_failure(self) -> None:
        """If a sub-app lifespan fails to start, others still work."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def broken_lifespan(app):
            raise RuntimeError("Startup failed")
            yield  # noqa: RET503 — unreachable but required by async gen

        sub_app = FastAPI(lifespan=broken_lifespan)

        @sub_app.get("/api/v1/volundr/ping")
        async def ping():
            return {"pong": True}

        class VolundrPlugin(FakePlugin):
            def create_api_app(self):
                return sub_app

        registry = PluginRegistry()
        registry.register(VolundrPlugin(name="volundr"))

        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()

        # Should not raise despite sub-app failure
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200


class TestSkuldWsProxy:
    """Tests for the Skuld WebSocket proxy endpoint."""

    def test_ws_proxy_session_not_found(self) -> None:
        registry = PluginRegistry()
        server = RootServer(registry=registry)
        with patch.dict(os.environ, {"NIUU_NO_WEB": "true"}):
            app = server._build_app()
        client = TestClient(app)
        with pytest.raises(Exception):
            # WebSocket to unknown session should close with 4004
            with client.websocket_connect("/s/unknown/session"):
                pass


class TestPluginApiPrefixes:
    """Tests for the _PLUGIN_API_PREFIXES constant."""

    def test_has_expected_plugins(self) -> None:
        assert "volundr" in _PLUGIN_API_PREFIXES
        assert "tyr" in _PLUGIN_API_PREFIXES
        assert "niuu" in _PLUGIN_API_PREFIXES

    def test_prefixes_start_with_api(self) -> None:
        for name, prefixes in _PLUGIN_API_PREFIXES.items():
            for prefix in prefixes:
                assert prefix.startswith("/api/v1/"), f"{name}: {prefix}"
