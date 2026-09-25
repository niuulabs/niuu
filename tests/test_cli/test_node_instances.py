"""Tests for cli.commands.node_instances.offered_instances_for_host."""

from __future__ import annotations

import pytest

from cli.commands.node_instances import UnreachableHostError, offered_instances_for_host
from cli.config import CLISettings


def test_raises_when_no_external_host_is_configured() -> None:
    """No `external_host or host` fallback: server.host is a bind address,
    not one another machine can reach, so there is no safe default."""
    settings = CLISettings()
    settings.server.host = "127.0.0.1"
    settings.server.external_host = ""

    with pytest.raises(UnreachableHostError, match="server.external_host"):
        offered_instances_for_host(settings)


def test_uses_the_configured_external_host() -> None:
    settings = CLISettings()
    settings.server.host = "0.0.0.0"
    settings.server.external_host = "spark-1.lan"
    settings.server.port = 8080

    offered = offered_instances_for_host(settings)

    assert len(offered) == 1
    assert offered[0].kind == "volundr"
    assert offered[0].base_url == "http://spark-1.lan:8080"
