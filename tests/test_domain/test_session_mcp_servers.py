"""A session's MCP servers, asked for by catalogue name."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.config import MCPServerEntry
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_mcp_servers import (
    UnknownMCPServerError,
    catalogue_entries,
    requested_mcp_servers,
)

PROGRAM = {
    "version": "0.46.0",
    "platforms": {
        "linux-x86_64": {
            "url": "https://downloads.example/cad.tar.gz",
            "sha256": "0" * 64,
            "command": "bin/cad",
        }
    },
}


def provider() -> ConfigMCPServerProvider:
    return ConfigMCPServerProvider(
        [
            MCPServerEntry(
                name="cad",
                args=["mcp", "--headless"],
                description="Parametric CAD",
                program=PROGRAM,
                env={"CAD_LOG": "warn"},
                default_tools_approval_mode="approve",
                startup_timeout_sec=30,
                tool_timeout_sec=600,
            ),
            MCPServerEntry(name="docs", type="http", url="https://docs.example/mcp"),
        ]
    )


def test_a_catalogue_server_is_passed_on_whole_and_required() -> None:
    assert catalogue_entries(["docs", "cad"], provider()) == [
        {"name": "docs", "type": "http", "required": True, "url": "https://docs.example/mcp"},
        {
            "name": "cad",
            "type": "stdio",
            "required": True,
            "description": "Parametric CAD",
            "program": PROGRAM,
            "env": {"CAD_LOG": "warn"},
            "args": ["mcp", "--headless"],
            "default_tools_approval_mode": "approve",
            "startup_timeout_sec": 30,
            "tool_timeout_sec": 600,
        },
    ]


def test_an_unknown_name_is_refused_with_the_names_there_are() -> None:
    with pytest.raises(UnknownMCPServerError, match="'cda' is not in .*it has: cad, docs"):
        catalogue_entries(["cda"], provider())
    with pytest.raises(UnknownMCPServerError, match="it has: none"):
        catalogue_entries(["cad"], None)
    assert catalogue_entries([], None) == []


def test_requested_names_are_read_once_each() -> None:
    assert requested_mcp_servers({}) == []
    assert requested_mcp_servers({"mcp_servers": ["cad", "docs", "cad"]}) == ["cad", "docs"]


@pytest.mark.parametrize("value", ["cad", [1], [""], {"cad": True}])
def test_anything_but_a_list_of_names_is_refused(value) -> None:
    with pytest.raises(ValueError, match="must be a list of MCP server names"):
        requested_mcp_servers({"mcp_servers": value})


@pytest.mark.parametrize("extra", [{"command": "cad"}, {"url": "https://x"}, {"type": "http"}])
def test_a_catalogue_program_is_its_servers_only_command(extra) -> None:
    with pytest.raises(ValidationError, match="leave out command, url and type"):
        MCPServerEntry(name="cad", program=PROGRAM, **extra)


def test_the_session_service_refuses_an_unknown_name() -> None:
    service = SessionService.__new__(SessionService)
    service._mcp_server_provider = provider()
    service.check_mcp_servers(["cad"])
    with pytest.raises(UnknownMCPServerError):
        service.check_mcp_servers(["cda"])
