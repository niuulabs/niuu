"""Tests for the Guild plugin."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from guild.plugin import GuildPlugin, _GuildStub
from niuu.cli_api_client import CLIAPIClient


def test_guild_plugin_metadata() -> None:
    plugin = GuildPlugin()
    assert plugin.name == "guild"
    assert "registry" in plugin.description.lower()


def test_guild_plugin_register_service() -> None:
    plugin = GuildPlugin()
    definition = plugin.register_service()
    assert definition.name == "guild"
    assert definition.default_enabled is True
    assert definition.default_port == 8084


def test_guild_plugin_create_service() -> None:
    plugin = GuildPlugin()
    assert isinstance(plugin.create_service(), _GuildStub)


def test_guild_plugin_create_api_app() -> None:
    plugin = GuildPlugin()
    sentinel = object()

    with patch("guild.app.create_app", return_value=sentinel):
        assert plugin.create_api_app() is sentinel


def test_guild_plugin_route_domains_are_stable_without_configured_workers() -> None:
    plugin = GuildPlugin()
    route_domains = plugin.api_route_domains()
    assert [route_domain.name for route_domain in route_domains] == [
        "guild-instances-api",
        "forge-api",
        "session-api",
        "ravn-aggregate-api",
    ]
    assert route_domains[0].prefixes == (
        "/api/v1/niuu/instances",
        "/api/v1/niuu/targets",
        "/api/v1/niuu/observatory",
    )
    assert route_domains[1].prefixes == ("/api/v1/forge",)
    assert route_domains[2].prefixes == (
        "/api/v1/forge/sessions",
        "/api/v1/forge/chronicles",
    )
    assert route_domains[3].prefixes == (
        "/api/v1/ravn/ravens",
        "/api/v1/ravn/sessions",
    )


def test_guild_plugin_route_domains_with_configured_workers(tmp_path, monkeypatch) -> None:
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

    route_domains = GuildPlugin().api_route_domains()

    assert [route_domain.name for route_domain in route_domains] == [
        "guild-instances-api",
        "forge-api",
        "session-api",
        "ravn-aggregate-api",
    ]
    assert route_domains[1].prefixes == ("/api/v1/forge",)
    assert route_domains[2].prefixes == (
        "/api/v1/forge/sessions",
        "/api/v1/forge/chronicles",
    )


def test_guild_plugin_create_api_client() -> None:
    plugin = GuildPlugin()

    client = plugin.create_api_client()

    assert isinstance(client, CLIAPIClient)
    assert client._base_url == "http://localhost:8080"
    assert client._service_name == "Guild"


@pytest.mark.asyncio
async def test_guild_stub_lifecycle_and_health_check() -> None:
    stub = _GuildStub()

    await stub.start()
    await stub.stop()

    assert await stub.health_check() is True
