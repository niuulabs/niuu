"""Tests for the discovery config `ravn peers` uses to query a live host."""

from __future__ import annotations

from types import SimpleNamespace

from ravn.cli.mesh_runtime import _query_adapters_config

_MDNS = "ravn.adapters.discovery.mdns.MdnsDiscoveryAdapter"
_STATIC = "ravn.adapters.discovery.static.StaticDiscoveryAdapter"


def _settings(adapters: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(discovery=SimpleNamespace(adapters=adapters))


class TestQueryAdaptersConfig:
    def test_mdns_handshake_port_is_made_ephemeral(self) -> None:
        """The live daemon already holds the configured port; reusing it collides."""
        config = _query_adapters_config(_settings([{"adapter": _MDNS, "handshake_port": 7580}]))

        assert config[0]["handshake_port"] == 0

    def test_the_original_config_is_not_mutated(self) -> None:
        adapters = [{"adapter": _MDNS, "handshake_port": 7580}]

        _query_adapters_config(_settings(adapters))

        assert adapters[0]["handshake_port"] == 7580

    def test_static_discovery_is_left_alone(self) -> None:
        """Static discovery binds nothing, so it needs no adjustment."""
        adapters = [{"adapter": _STATIC, "cluster_file": "/tmp/cluster.yaml"}]

        config = _query_adapters_config(_settings(adapters))

        assert config == adapters

    def test_mixed_adapters_are_handled_independently(self) -> None:
        config = _query_adapters_config(
            _settings(
                [
                    {"adapter": _STATIC, "cluster_file": "/tmp/c.yaml"},
                    {"adapter": _MDNS, "handshake_port": 7580},
                ]
            )
        )

        assert "handshake_port" not in config[0]
        assert config[1]["handshake_port"] == 0

    def test_no_adapters_yields_empty(self) -> None:
        assert _query_adapters_config(_settings([])) == []
