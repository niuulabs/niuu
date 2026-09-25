"""Tests for cli.commands.node_instances.offered_instances_for_host."""

from __future__ import annotations

from cli.commands.node_instances import offered_instances_for_host
from cli.config import CLISettings


def test_uses_the_bind_host_when_no_external_host_is_configured() -> None:
    settings = CLISettings()
    settings.server.host = "127.0.0.1"
    settings.server.port = 9090
    settings.server.external_host = ""

    offered = offered_instances_for_host(settings)

    assert len(offered) == 1
    assert offered[0].kind == "volundr"
    assert offered[0].base_url == "http://127.0.0.1:9090"


def test_prefers_the_externally_reachable_host() -> None:
    settings = CLISettings()
    settings.server.host = "0.0.0.0"
    settings.server.external_host = "spark-1.lan"
    settings.server.port = 8080

    offered = offered_instances_for_host(settings)

    assert offered[0].base_url == "http://spark-1.lan:8080"
