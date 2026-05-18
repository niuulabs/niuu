"""Tests for the Guild plugin."""

from __future__ import annotations

from unittest.mock import patch

from niuu.cli_api_client import CLIAPIClient
from niuu.guild_plugin import GuildPlugin, _GuildStub


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

    with patch("niuu.guild_app.create_app", return_value=sentinel):
        assert plugin.create_api_app() is sentinel


def test_guild_plugin_route_domains() -> None:
    plugin = GuildPlugin()
    route_domains = plugin.api_route_domains()
    assert [route_domain.name for route_domain in route_domains] == [
        "guild-instances-api",
        "guild-volundr-api",
    ]
    assert route_domains[0].prefixes == (
        "/api/v1/niuu/instances",
        "/api/v1/niuu/targets",
        "/api/v1/niuu/observatory",
    )
    assert route_domains[1].prefixes == ("/api/v1/niuu/volundr",)


def test_guild_plugin_create_api_client() -> None:
    plugin = GuildPlugin()

    client = plugin.create_api_client()

    assert isinstance(client, CLIAPIClient)
    assert client._base_url == "http://localhost:8080"
    assert client._service_name == "Guild"
