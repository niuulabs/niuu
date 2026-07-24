"""Tests for volundr.plugin, ting.plugin, and the plugin registry."""

from __future__ import annotations

import httpx
import respx
import typer
from typer.testing import CliRunner

from audit.plugin import AuditPlugin
from bifrost.plugin import BifrostPlugin
from cli.registry import PluginRegistry
from credentials.plugin import CredentialsPlugin
from features.plugin import FeaturesPlugin
from identity.plugin import IdentityPlugin
from integrations.plugin import IntegrationsPlugin
from mimir.plugin import MimirPlugin
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServiceLifecycle
from observatory.plugin import ObservatoryPlugin
from personas.plugin import PersonasPlugin
from ravn.plugin import RavnPlugin
from ting.plugin import TingPlugin
from tracker.plugin import TrackerPlugin
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

    def test_service_is_host_mounted(self) -> None:
        plugin = VolundrPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

    def test_create_api_app_uses_volundr_main_factory(self, monkeypatch) -> None:
        plugin = VolundrPlugin()
        sentinel = object()

        def fake_create_app(*, public_origin: str):
            assert public_origin == "http://localhost:8080"
            return sentinel

        monkeypatch.setattr("volundr.main.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = VolundrPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "admin-api",
            "catalog-api",
        ]
        domains = {route_domain.name: route_domain.prefixes for route_domain in route_domains}
        assert domains["admin-api"] == ("/api/v1/forge/admin", "/api/v1/forge/settings")
        assert domains["catalog-api"] == (
            "/api/v1/volundr/launch-specs",
            "/api/v1/volundr/session-definitions",
            "/api/v1/volundr/resources",
            "/api/v1/volundr/prompts",
        )

    def test_api_route_domains_are_stable_when_workers_configured(
        self, tmp_path, monkeypatch
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
niuu:
  instances:
    - kind: volundr
      slug: alpha
      name: Alpha
      base_url: http://127.0.0.1:8181
      enabled: true
""".lstrip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("NIUU_CONFIG", str(config_path))

        route_domains = VolundrPlugin().api_route_domains()

        assert [route_domain.name for route_domain in route_domains] == [
            "admin-api",
            "catalog-api",
        ]

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


class TestVolundrDomainPlugins:
    def test_audit_plugin_route_domains(self) -> None:
        plugin = AuditPlugin()
        assert plugin.api_route_domains() == (
            APIRouteDomain(
                name="audit-api",
                prefixes=("/api/v1/audit", "/audit"),
                description="Canonical audit log query routes.",
            ),
        )

    def test_identity_plugin_route_domains(self) -> None:
        plugin = IdentityPlugin()
        route_domains = plugin.api_route_domains()
        assert [route_domain.name for route_domain in route_domains] == [
            "identity-api",
            "tenancy-api",
            "tokens-api",
        ]
        assert route_domains[0].prefixes == (
            "/api/v1/identity/me",
            "/api/v1/identity/settings",
            "/api/v1/identity/auth/config",
            "/api/v1/identity/users",
        )
        assert route_domains[1].prefixes == ("/api/v1/identity/tenants",)
        assert route_domains[2].prefixes == ("/api/v1/tokens",)

    def test_features_plugin_route_domains(self) -> None:
        plugin = FeaturesPlugin()
        assert plugin.api_route_domains() == (
            APIRouteDomain(
                name="features-api",
                prefixes=("/api/v1/features",),
                description="Canonical feature catalog and preferences routes.",
            ),
        )

    def test_credentials_plugin_route_domains(self) -> None:
        plugin = CredentialsPlugin()
        assert plugin.api_route_domains() == (
            APIRouteDomain(
                name="credentials-api",
                prefixes=("/api/v1/credentials",),
                description="Canonical credential and secret-type routes.",
            ),
        )

    def test_integrations_plugin_route_domains(self) -> None:
        plugin = IntegrationsPlugin()
        assert plugin.api_route_domains() == (
            APIRouteDomain(
                name="integrations-api",
                prefixes=("/api/v1/integrations",),
                description="Canonical integrations and OAuth routes.",
            ),
        )

    def test_tracker_plugin_route_domains(self) -> None:
        plugin = TrackerPlugin()
        assert plugin.api_route_domains() == (
            APIRouteDomain(
                name="tracker-api",
                prefixes=(
                    "/api/v1/tracker/status",
                    "/api/v1/tracker/issues",
                    "/api/v1/tracker/repo-mappings",
                ),
                description="Canonical tracker issue, status, and repo mapping routes.",
            ),
        )


class TestTingPlugin:
    def test_name(self) -> None:
        plugin = TingPlugin()
        assert plugin.name == "ting"

    def test_description(self) -> None:
        plugin = TingPlugin()
        desc = plugin.description.lower()
        assert "saga" in desc or "coordinator" in desc

    def test_register_service_returns_definition(self) -> None:
        plugin = TingPlugin()
        svc_def = plugin.register_service()
        assert isinstance(svc_def, ServiceDefinition)
        assert svc_def.name == "ting"
        assert svc_def.default_enabled is True

    def test_register_service_depends_on_volundr(self) -> None:
        plugin = TingPlugin()
        svc_def = plugin.register_service()
        assert svc_def is not None
        assert "volundr" in svc_def.depends_on

    def test_depends_on_volundr_via_service_definition(self) -> None:
        plugin = TingPlugin()
        assert "volundr" in plugin.depends_on()

    def test_service_is_host_mounted(self) -> None:
        plugin = TingPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

    def test_api_route_domains_declared(self) -> None:
        plugin = TingPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "tracker-intake-api",
            "saga-api",
            "review-api",
            "dispatch-api",
            "workflow-api",
            "settings-api",
            "ting-channel-api",
            "event-api",
            "a2a-card-api",
            "ting-api",
        ]
        assert route_domains[0].prefixes == ("/api/v1/tracker/projects", "/api/v1/tracker/import")
        assert route_domains[4].prefixes == (
            "/api/v1/ting/flock",
            "/api/v1/ting/flock_flows",
            "/api/v1/ting/research",
            "/api/v1/ting/specs",
        )
        assert route_domains[5].prefixes == ("/api/v1/ting/settings",)
        assert route_domains[6].prefixes == ("/api/v1/ting/telegram",)

    def test_registers_sagas_group(self) -> None:
        plugin = TingPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        group_names = [g.name for g in app.registered_groups]
        assert "sagas" in group_names

    def test_registers_runs_group(self) -> None:
        plugin = TingPlugin()
        app = typer.Typer()
        plugin.register_commands(app)
        group_names = [g.name for g in app.registered_groups]
        assert "runs" in group_names

    @respx.mock
    def test_sagas_list_command(self) -> None:
        respx.get(f"{BASE}/api/v1/ting/sagas").mock(return_value=httpx.Response(200, json=[]))
        plugin = TingPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["sagas", "list"])
        assert result.exit_code == 0

    @respx.mock
    def test_runs_active_command(self) -> None:
        respx.get(f"{BASE}/api/v1/ting/runs/active").mock(return_value=httpx.Response(200, json=[]))
        plugin = TingPlugin()
        app = typer.Typer(no_args_is_help=False)
        plugin.register_commands(app)
        result = runner.invoke(app, ["runs", "active"])
        assert result.exit_code == 0

    def test_api_client_returns_instance(self) -> None:
        plugin = TingPlugin()
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

    def test_service_is_host_mounted(self) -> None:
        plugin = MimirPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

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

    def test_service_is_host_mounted(self) -> None:
        plugin = RavnPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

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
            "ravn-valkyrie-api",
            "ravn-odin-api",
            "ravn-session-api",
            "ravn-api",
        ]
        assert route_domains[0].prefixes == (
            "/api/v1/ravn/ravens",
            "/api/v1/ravn/sessions",
        )
        assert route_domains[1].prefixes == ("/api/v1/ravn/triggers",)
        assert route_domains[2].prefixes == ("/api/v1/ravn/budget",)
        assert route_domains[3].prefixes == ("/api/v1/ravn/valkyrie",)
        assert route_domains[4].prefixes == ("/api/v1/ravn/odin",)
        assert route_domains[5].prefixes == (
            "/api/v1/ravn/status",
            "/api/v1/ravn/sessions",
        )
        assert route_domains[6].prefixes == ("/api/v1/ravn",)

    def test_api_client_returns_instance(self) -> None:
        plugin = RavnPlugin()
        client = plugin.create_api_client()
        assert client is not None

    def test_depends_on_returns_postgres(self) -> None:
        plugin = RavnPlugin()
        assert list(plugin.depends_on()) == ["postgres"]

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

    def test_service_is_host_mounted(self) -> None:
        plugin = PersonasPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

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

    def test_service_is_host_mounted(self) -> None:
        plugin = BifrostPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

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

    def test_service_is_host_mounted(self) -> None:
        plugin = ObservatoryPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

    def test_create_api_app_uses_observatory_app_factory(self, monkeypatch) -> None:
        plugin = ObservatoryPlugin()
        sentinel = object()

        def fake_create_app(**kwargs):
            assert "discovery_service" in kwargs
            assert kwargs["discovery_service"] is not None
            return sentinel

        monkeypatch.setattr("observatory.app.create_app", fake_create_app)
        assert plugin.create_api_app() is sentinel

    def test_api_route_domains_declared(self) -> None:
        plugin = ObservatoryPlugin()
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert [route_domain.name for route_domain in route_domains] == [
            "observatory-agents-api",
            "observatory-registry-api",
            "observatory-topology-api",
            "observatory-events-api",
            "observatory-api",
        ]
        assert route_domains[0].prefixes == ("/api/v1/observatory/agents",)
        assert route_domains[1].prefixes == ("/api/v1/observatory/registry",)
        assert route_domains[2].prefixes == (
            "/api/v1/observatory/topology/stream",
            "/api/v1/observatory/topology",
        )
        assert route_domains[3].prefixes == (
            "/api/v1/observatory/events/stream",
            "/api/v1/observatory/events",
        )
        assert route_domains[4].prefixes == ("/api/v1/observatory",)

    def test_api_client_returns_instance(self) -> None:
        plugin = ObservatoryPlugin()
        client = plugin.create_api_client()
        assert client is not None


class TestPluginDiscovery:
    def test_both_plugins_register(self) -> None:
        registry = PluginRegistry()
        registry.register(VolundrPlugin())
        registry.register(TingPlugin())
        assert "volundr" in registry.plugins
        assert "ting" in registry.plugins

    def test_ravn_plugin_registers(self) -> None:
        registry = PluginRegistry()
        registry.register(RavnPlugin())
        assert "ravn" in registry.plugins

    def test_dependency_order(self) -> None:
        """Ting depends on volundr — verify start order."""
        from cli.services.manager import ServiceManager

        registry = PluginRegistry()
        registry.register(VolundrPlugin())
        registry.register(TingPlugin())
        manager = ServiceManager(registry=registry)
        order = manager.resolve_start_order()
        assert order.index("volundr") < order.index("ting")

    def test_plugin_without_register_service_works(self) -> None:
        """A plugin that does not implement register_service() still works."""
        from tests.test_cli.conftest import FakePlugin

        plugin = FakePlugin(name="query-only")
        assert plugin.register_service() is None
        assert list(plugin.depends_on()) == []
