"""Tests for the standalone PersonasPlugin."""

from __future__ import annotations

from fastapi import FastAPI

from niuu.ports.plugin import ServiceDefinition
from personas.plugin import PersonasPlugin, _PersonasStub


def test_plugin_name() -> None:
    plugin = PersonasPlugin()
    assert plugin.name == "personas"


def test_plugin_description() -> None:
    plugin = PersonasPlugin()
    assert "persona" in plugin.description.lower()


def test_register_service_returns_definition() -> None:
    plugin = PersonasPlugin()
    defn = plugin.register_service()
    assert isinstance(defn, ServiceDefinition)
    assert defn.name == "personas"


async def test_stub_health_check_returns_true() -> None:
    svc = _PersonasStub()
    assert await svc.health_check() is True


def test_create_api_app_returns_fastapi(monkeypatch) -> None:
    plugin = PersonasPlugin()
    sentinel = FastAPI()
    monkeypatch.setattr("personas.app.create_app", lambda: sentinel)
    assert plugin.create_api_app() is sentinel


def test_api_route_domains_expose_canonical_and_legacy_prefixes() -> None:
    plugin = PersonasPlugin()
    domains = plugin.api_route_domains()
    assert len(domains) == 1
    assert domains[0].name == "persona-api"
    assert domains[0].prefixes == ("/api/v1/personas", "/api/v1/ravn/personas")


def test_create_api_client_returns_client() -> None:
    plugin = PersonasPlugin()
    client = plugin.create_api_client()
    assert client is not None
