"""Config-driven MCP server provider adapter."""

from __future__ import annotations

from credentials.models import MCPServerConfig
from credentials.ports import MCPServerProvider
from volundr.config import MCPServerEntry


class ConfigMCPServerProvider(MCPServerProvider):
    """Reads available MCP servers from YAML/CRD config."""

    def __init__(self, configs: list[MCPServerEntry]) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        for cfg in configs:
            self._servers[cfg.name] = MCPServerConfig(
                name=cfg.name,
                type=cfg.type,
                command=cfg.command,
                url=cfg.url,
                args=cfg.args,
                description=cfg.description,
            )

    def list(self) -> list[MCPServerConfig]:
        """Return all available MCP server configs sorted by name."""
        return sorted(self._servers.values(), key=lambda s: s.name)

    def get(self, name: str) -> MCPServerConfig | None:
        """Return a specific MCP server config by name."""
        return self._servers.get(name)
