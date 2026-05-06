"""Tests for volundr.plugin, tyr.plugin, and the plugin registry."""

from __future__ import annotations

import asyncio

import httpx
import respx
import typer
from typer.testing import CliRunner

from bifrost.plugin import BifrostPlugin
from cli.registry import PluginRegistry
from mimir.plugin import MimirPlugin
from niuu.ports.plugin import ServiceDefinition
from observatory.plugin import ObservatoryPlugin
from personas.plugin import PersonasPlugin
from ravn.plugin import RavnPlugin
from tyr.plugin import TyrPlugin
from volundr.plugin import VolundrPlugin

runner = CliRunner()
BASE = "http://localhost:8080"


class TestVolundrPlugin:
    def test_name(self) -> None:
        plugin = VolundrPlugin()
        assert plugin.name == "volundr"

    def test_description(self) -> None:
        plugin = VolundrPlugin()
        desc = plugin.description.lower()
        assert "session" in desc or "development" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = VolundrPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "volundr"
        assert svc_def.default_enabled is True

    def test_register_service_default_port(self) -> None:
        plugin = VolundrPlugin()
        svc_def = plugin.register_service()
        assert svc_def is not None
        assert svc_def.default_port > 0

    def test_register_service_factory_creates_service(self) -> None:
        plugin = VolundrPlugin()
        svc_def = plugin.register_service()
        assert svc_def is not None
        svc = svc_def.factory()
        assert svc is not None

    def test_depends_on_via_service_definition(self) -> None:
        plugin = VolundrPlugin()
        # depends_on() delegates to register_service().depends_on
        svc_def = plugin.register_service()
        assert svc_def is not None
        assert list(plugin.depends_on()) == svc_def.depends_on

    def test_create_service_returns_service(self) -> None:
        plugin = VolundrPlugin()
        svc = plugin.create_service()
        assert svc is not None

    def test_create_service_stub_health_check(self) -> None:
        plugin = VolundrPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_volundr_main_factory(self, monkeypatch) -> None:
        plugin = VolundrPlugin()
        sentinel = object()

        def fake_create_app():
            return sentinel

        monkeypatch.setattr("volundr.main.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = VolundrPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "audit-api",
            "admin-api",
            "features-api",
            "features-legacy-api",
            "credentials-api",
            "credentials-legacy-api",
            "forge-api",
            "forge-legacy-api",
            "session-api",
            "session-legacy-api",
            "workspace-api",
            "workspace-legacy-api",
            "catalog-api",
            "catalog-legacy-api",
            "git-api",
            "git-legacy-api",
            "volundr-api",
            "identity-api",
            "identity-legacy-api",
            "integrations-api",
            "integrations-legacy-api",
            "tenancy-api",
            "tracker-api",
            "tokens-api",
            "tokens-legacy-api",
        ]
        domains = {route_domain.name: route_domain.prefixes for route_domain in route_domains}
        assert domains["audit-api"] == ("/api/v1/audit", "/audit")
        assert domains["features-api"] == ("/api/v1/features",)
        assert domains["features-legacy-api"] == ("/api/v1/volundr/features",)
        assert domains["credentials-api"] == ("/api/v1/credentials",)
        assert domains["credentials-legacy-api"] == (
            "/api/v1/volundr/credentials",
            "/api/v1/volundr/secrets",
        )
        assert domains["forge-api"][:3] == (
            "/api/v1/forge/sessions",
            "/api/v1/forge/chronicles",
            "/api/v1/forge/events",
        )
        assert domains["forge-legacy-api"][:3] == (
            "/api/v1/volundr/sessions",
            "/api/v1/volundr/chronicles",
            "/api/v1/volundr/events",
        )
        assert domains["session-api"] == (
            "/api/v1/forge/sessions",
            "/api/v1/forge/chronicles",
            "/api/v1/forge/events",
        )
        assert domains["session-legacy-api"] == (
            "/api/v1/volundr/sessions",
            "/api/v1/volundr/chronicles",
            "/api/v1/volundr/events",
        )
        assert domains["workspace-api"] == ("/api/v1/forge/workspaces",)
        assert domains["workspace-legacy-api"] == ("/api/v1/volundr/workspaces",)
        assert domains["identity-api"] == ("/api/v1/identity",)
        assert domains["identity-legacy-api"] == ("/api/v1/volundr/me", "/api/v1/volundr/identity")
        assert domains["integrations-api"] == ("/api/v1/integrations",)
        assert domains["integrations-legacy-api"] == ("/api/v1/volundr/integrations",)
        assert domains["tokens-api"] == ("/api/v1/tokens",)
        assert domains["tokens-legacy-api"] == ("/api/v1/users/tokens", "/api/v1/volundr/tokens")

    def test_registers_sessions_group(self) -> None:
        plugin = VolundrPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        # sessions should be registered as a sub-group
        group_names = [g.name for g in app.registered_groups]
        assert "sessions" in group_names

    @respx.mock
    def test_sessions_list_command(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(200, json=[]))
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "list"])
        assert result.exit_code == 0

    @respx.mock
    def test_sessions_list_command_json_output(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[{"id": "s1", "name": "demo"}])
        )
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "list", "--json"])
        assert result.exit_code == 0
        assert '"id": "s1"' in result.stdout

    @respx.mock
    def test_sessions_create_command(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(201, json={"id": "s1", "name": "my-session"})
        )
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "create", "my-session"])
        assert result.exit_code == 0

    @respx.mock
    def test_sessions_create_command_json_output(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(201, json={"id": "s1", "name": "my-session"})
        )
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "create", "my-session", "--json"])
        assert result.exit_code == 0
        assert '"id": "s1"' in result.stdout

    @respx.mock
    def test_sessions_stop_command(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions/s1/stop").mock(
            return_value=httpx.Response(200, json={"status": "stopped"})
        )
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "stop", "s1"])
        assert result.exit_code == 0

    @respx.mock
    def test_sessions_delete_command_json_output(self) -> None:
        respx.delete(f"{BASE}/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"status": "deleted"})
        )
        plugin = VolundrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sessions", "delete", "s1", "--json"])
        assert result.exit_code == 0
        assert '"status": "deleted"' in result.stdout

    def test_api_client_returns_instance(self) -> None:
        plugin = VolundrPlugin()
        client = plugin.create_api_client()
        assert client is not None

    def test_tui_pages_registered(self) -> None:
        plugin = VolundrPlugin()
        pages = plugin.tui_pages()
        assert len(pages) == 7
        names = [p.name for p in pages]
        assert "Sessions" in names
        assert "Chat" in names


class TestTyrPlugin:
    def test_name(self) -> None:
        plugin = TyrPlugin()
        assert plugin.name == "tyr"

    def test_description(self) -> None:
        plugin = TyrPlugin()
        desc = plugin.description.lower()
        assert "saga" in desc or "coordinator" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = TyrPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "tyr"
        assert svc_def.default_enabled is True

    def test_register_service_depends_on_volundr(self) -> None:
        plugin = TyrPlugin()
        svc_def = plugin.register_service()
        assert svc_def is not None
        assert "volundr" in svc_def.depends_on

    def test_depends_on_volundr_via_service_definition(self) -> None:
        plugin = TyrPlugin()
        assert "volundr" in plugin.depends_on()

    def test_create_service_returns_service(self) -> None:
        plugin = TyrPlugin()
        svc = plugin.create_service()
        assert svc is not None

    def test_api_route_domains_declared(self) -> None:
        plugin = TyrPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "tracker-api",
            "saga-api",
            "review-api",
            "dispatch-api",
            "workflow-api",
            "settings-api",
            "integrations-api",
            "event-api",
            "tyr-api",
        ]
        assert route_domains[0].prefixes == ("/api/v1/tracker/projects", "/api/v1/tracker/import")
        assert route_domains[4].prefixes == (
            "/api/v1/tyr/flock",
            "/api/v1/tyr/flock_flows",
            "/api/v1/tyr/pipelines",
        )
        assert route_domains[5].prefixes == ("/api/v1/tyr/settings",)

    def test_registers_sagas_group(self) -> None:
        plugin = TyrPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        group_names = [g.name for g in app.registered_groups]
        assert "sagas" in group_names

    def test_registers_raids_group(self) -> None:
        plugin = TyrPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        group_names = [g.name for g in app.registered_groups]
        assert "raids" in group_names

    @respx.mock
    def test_sagas_list_command(self) -> None:
        respx.get(f"{BASE}/api/v1/tyr/sagas").mock(return_value=httpx.Response(200, json=[]))
        plugin = TyrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sagas", "list"])
        assert result.exit_code == 0

    @respx.mock
    def test_raids_active_command(self) -> None:
        respx.get(f"{BASE}/api/v1/tyr/raids/active").mock(return_value=httpx.Response(200, json=[]))
        plugin = TyrPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["raids", "active"])
        assert result.exit_code == 0

    def test_api_client_returns_instance(self) -> None:
        plugin = TyrPlugin()
        client = plugin.create_api_client()
        assert client is not None


class TestMimirPlugin:
    def test_name(self) -> None:
        plugin = MimirPlugin()
        assert plugin.name == "mimir"

    def test_description(self) -> None:
        plugin = MimirPlugin()
        desc = plugin.description.lower()
        assert "knowledge" in desc or "search" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = MimirPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "mimir"
        assert svc_def.default_enabled is True

    def test_create_service_stub_health_check(self) -> None:
        plugin = MimirPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_mimir_app_factory(self, monkeypatch) -> None:
        plugin = MimirPlugin()
        sentinel = object()

        def fake_create_app(_config):
            return sentinel

        monkeypatch.setattr("mimir.app.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = MimirPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == ["mimir-api"]
        assert route_domains[0].prefixes == (
            "/api/v1/mimir/mcp",
            "/api/v1/mimir",
            "/mcp",
            "/mimir",
        )

    def test_api_client_returns_instance(self) -> None:
        plugin = MimirPlugin()
        client = plugin.create_api_client()
        assert client is not None


class TestRavnPlugin:
    def test_name(self) -> None:
        plugin = RavnPlugin()
        assert plugin.name == "ravn"

    def test_description(self) -> None:
        plugin = RavnPlugin()
        desc = plugin.description.lower()
        assert "agent" in desc or "session" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = RavnPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "ravn"
        assert svc_def.default_enabled is True

    def test_create_service_stub_health_check(self) -> None:
        plugin = RavnPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_ravn_app_factory(self, monkeypatch) -> None:
        plugin = RavnPlugin()
        sentinel = object()

        def fake_create_app():
            return sentinel

        monkeypatch.setattr("ravn.api.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = RavnPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "ravn-runtime-api",
            "ravn-trigger-api",
            "ravn-budget-api",
            "ravn-session-api",
            "ravn-api",
        ]
        assert route_domains[0].prefixes == (
            "/api/v1/ravn/ravens",
            "/api/v1/ravn/sessions",
        )
        assert route_domains[1].prefixes == ("/api/v1/ravn/triggers",)
        assert route_domains[2].prefixes == ("/api/v1/ravn/budget",)
        assert route_domains[3].prefixes == (
            "/api/v1/ravn/status",
            "/api/v1/ravn/sessions",
        )
        assert route_domains[4].prefixes == ("/api/v1/ravn",)

    def test_api_client_returns_instance(self) -> None:
        plugin = RavnPlugin()
        client = plugin.create_api_client()
        assert client is not None

    def test_depends_on_is_empty(self) -> None:
        plugin = RavnPlugin()
        assert list(plugin.depends_on()) == []

    def test_registers_ravn_group(self) -> None:
        plugin = RavnPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        group_names = [g.name for g in app.registered_groups]
        assert "ravn" in group_names

    @respx.mock
    def test_ravn_list_command(self) -> None:
        respx.get(f"{BASE}/api/v1/ravn/sessions").mock(return_value=httpx.Response(200, json=[]))
        plugin = RavnPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["ravn", "list"])
        assert result.exit_code == 0
        assert "No active agent sessions." in result.stdout

    @respx.mock
    def test_ravn_list_command_json_output(self) -> None:
        respx.get(f"{BASE}/api/v1/ravn/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "ravn-1",
                        "status": "running",
                        "model": "gpt-5",
                        "created_at": "2026-04-25T00:00:00Z",
                    }
                ],
            )
        )
        plugin = RavnPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["ravn", "list", "--json"])
        assert result.exit_code == 0
        assert '"id": "ravn-1"' in result.stdout

    @respx.mock
    def test_ravn_stop_command_json_output(self) -> None:
        respx.post(f"{BASE}/api/v1/ravn/sessions/ravn-1/stop").mock(
            return_value=httpx.Response(200, json={"session_id": "ravn-1", "status": "stopped"})
        )
        plugin = RavnPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["ravn", "stop", "ravn-1", "--json"])
        assert result.exit_code == 0
        assert '"status": "stopped"' in result.stdout

    @respx.mock
    def test_ravn_status_command(self) -> None:
        respx.get(f"{BASE}/api/v1/ravn/status").mock(
            return_value=httpx.Response(
                200,
                json={"service": "ravn", "session_count": 2, "healthy": True},
            )
        )
        plugin = RavnPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["ravn", "status"])
        assert result.exit_code == 0
        assert "2 active session(s)" in result.stdout

    def test_tui_pages_registered(self) -> None:
        plugin = RavnPlugin()
        pages = plugin.tui_pages()
        assert len(pages) == 1
        assert pages[0].name == "Agents"


class TestPersonasPlugin:
    def test_name(self) -> None:
        plugin = PersonasPlugin()
        assert plugin.name == "personas"

    def test_description(self) -> None:
        plugin = PersonasPlugin()
        assert "persona" in plugin.description.lower()

    def test_register_service_returns_definition(self) -> None:
        plugin = PersonasPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "personas"
        assert svc_def.default_enabled is True

    def test_create_service_stub_health_check(self) -> None:
        plugin = PersonasPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_personas_app_factory(self, monkeypatch) -> None:
        plugin = PersonasPlugin()
        sentinel = object()

        def fake_create_app():
            return sentinel

        monkeypatch.setattr("personas.app.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = PersonasPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == ["persona-api"]
        assert route_domains[0].prefixes == ("/api/v1/personas", "/api/v1/ravn/personas")

    def test_api_client_returns_instance(self) -> None:
        plugin = PersonasPlugin()
        client = plugin.create_api_client()
        assert client is not None


class TestBifrostPlugin:
    def test_name(self) -> None:
        plugin = BifrostPlugin()
        assert plugin.name == "bifrost"

    def test_description(self) -> None:
        plugin = BifrostPlugin()
        desc = plugin.description.lower()
        assert "proxy" in desc or "gateway" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = BifrostPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "bifrost"
        assert svc_def.default_enabled is True

    def test_create_service_stub_health_check(self) -> None:
        plugin = BifrostPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_bifrost_app_factory(self, monkeypatch) -> None:
        plugin = BifrostPlugin()
        sentinel = object()

        def fake_create_app(_config):
            return sentinel

        monkeypatch.setattr("bifrost.app.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = BifrostPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "llm-api",
            "bifrost-observability-api",
            "bifrost-api",
        ]
        assert route_domains[0].prefixes == (
            "/api/v1/bifrost/health",
            "/api/v1/bifrost/admin",
            "/api/v1/bifrost/v1",
            "/api/v1/bifrost/api",
        )
        assert route_domains[1].prefixes == (
            "/api/v1/bifrost/metrics",
            "/api/v1/bifrost/healthz",
            "/api/v1/bifrost/readyz",
        )
        assert route_domains[2].prefixes == ("/api/v1/bifrost",)


class TestObservatoryPlugin:
    def test_name(self) -> None:
        plugin = ObservatoryPlugin()
        assert plugin.name == "observatory"

    def test_description(self) -> None:
        plugin = ObservatoryPlugin()
        assert "registry" in plugin.description.lower()

    def test_register_service_returns_definition(self) -> None:
        plugin = ObservatoryPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "observatory"
        assert svc_def.default_enabled is True

    def test_create_service_stub_health_check(self) -> None:
        plugin = ObservatoryPlugin()
        svc = plugin.create_service()
        asyncio.run(svc.start())
        assert asyncio.run(svc.health_check()) is True
        asyncio.run(svc.stop())

    def test_create_api_app_uses_observatory_app_factory(self, monkeypatch) -> None:
        plugin = ObservatoryPlugin()
        sentinel = object()

        def fake_create_app():
            return sentinel

        monkeypatch.setattr("observatory.app.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = ObservatoryPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "observatory-registry-api",
            "observatory-topology-api",
            "observatory-events-api",
            "observatory-api",
        ]
        assert route_domains[0].prefixes == ("/api/v1/observatory/registry",)
        assert route_domains[1].prefixes == (
            "/api/v1/observatory/topology/stream",
            "/api/v1/observatory/topology",
        )
        assert route_domains[2].prefixes == (
            "/api/v1/observatory/events/stream",
            "/api/v1/observatory/events",
        )
        assert route_domains[3].prefixes == ("/api/v1/observatory",)

    def test_api_client_returns_instance(self) -> None:
        plugin = ObservatoryPlugin()
        client = plugin.create_api_client()
        assert client is not None


class TestPluginDiscovery:
    def test_both_plugins_register(self) -> None:
        registry = PluginRegistry()
        registry.register(VolundrPlugin())
        registry.register(TyrPlugin())
        assert "volundr" in registry.plugins
        assert "tyr" in registry.plugins

    def test_ravn_plugin_registers(self) -> None:
        registry = PluginRegistry()
        registry.register(RavnPlugin())
        assert "ravn" in registry.plugins

    def test_dependency_order(self) -> None:
        """Tyr depends on volundr — verify start order."""
        from cli.services.manager import ServiceManager

        registry = PluginRegistry()
        registry.register(VolundrPlugin())
        registry.register(TyrPlugin())
        manager = ServiceManager(registry=registry)
        order = manager.resolve_start_order()
        assert order.index("volundr") < order.index("tyr")

    def test_plugin_without_register_service_works(self) -> None:
        """A plugin that does not implement register_service() still works."""
        from tests.test_cli.conftest import FakePlugin

        plugin = FakePlugin(name="query-only")
        assert plugin.register_service() is None
        assert list(plugin.depends_on()) == []
