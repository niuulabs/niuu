"""Tests for the Observatory plugin surface."""

from __future__ import annotations

from fastapi import FastAPI

from niuu.ports.plugin import ServiceDefinition, ServiceLifecycle
from observatory.plugin import ObservatoryPlugin


def test_plugin_name() -> None:
    plugin = ObservatoryPlugin()
    assert plugin.name == "observatory"


def test_plugin_description() -> None:
    plugin = ObservatoryPlugin()
    assert "topology" in plugin.description.lower()


def test_register_service_returns_definition() -> None:
    plugin = ObservatoryPlugin()
    definition = plugin.register_service()
    assert isinstance(definition, ServiceDefinition)
    assert definition.name == "observatory"
    assert definition.depends_on == ["postgres"]


def test_service_is_host_mounted() -> None:
    plugin = ObservatoryPlugin()
    definition = plugin.register_service()
    assert definition.lifecycle is ServiceLifecycle.HOSTED
    assert definition.factory is None
    assert plugin.create_service() is None


def test_create_api_app_returns_fastapi(monkeypatch) -> None:
    plugin = ObservatoryPlugin()
    sentinel = FastAPI()
    captured: dict[str, object] = {}

    def fake_create_app(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("observatory.app.create_app", fake_create_app)
    assert plugin.create_api_app(base_url="http://platform.test:8080") is sentinel
    discovery_service = captured["discovery_service"]
    assert discovery_service.guild_url == "http://localhost:8080"


def test_api_route_domains_include_registry_topology_and_events() -> None:
    plugin = ObservatoryPlugin()
    domains = plugin.api_route_domains()
    assert len(domains) == 4
    assert domains[0].name == "observatory-registry-api"
    assert "/api/v1/observatory/topology/stream" in domains[1].prefixes
    assert "/api/v1/observatory/events" in domains[2].prefixes
    assert domains[3].prefixes == ("/api/v1/observatory",)


def test_create_api_client_returns_cli_client() -> None:
    plugin = ObservatoryPlugin()
    client = plugin.create_api_client()
    assert client is not None
    assert client._service_name == "Observatory"
