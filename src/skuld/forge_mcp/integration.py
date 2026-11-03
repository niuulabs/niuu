"""Broker wiring for the session tools and the built-in ``forge`` MCP server.

Per engine (``_build_transport_kwargs`` hands every transport the merged
``mcp_servers`` list and the :class:`SessionTools`; each translator does the rest):

* Claude — tmux, Agent SDK, subprocess, persistent subprocess, sdk-websocket: the
  ``forge`` stdio server via a 0600 ``--mcp-config`` file, the forge-notify skill via
  ``--plugin-dir`` (SDK: ``plugins``), capability text via ``--append-system-prompt``
  (SDK: ``system_prompt``), present-file env, and authenticated HTTP hooks (tmux).
* Codex — app-server: ``-c mcp_servers.forge.*``, ``developerInstructions``, the skill
  via ``skills/extraRoots/set``; exec: ``-c mcp_servers.forge.*`` and
  ``-c developer_instructions`` (no per-session skill API).
* Grok ACP: ``session/new`` ``mcpServers`` (stdio form). ACP has no standard system
  prompt field, so no capability text is added; the tool descriptions carry it.
* OpenCode, Muse, Pi, dsh and Claude remote-control: no MCP injection (their
  transports take no MCP configuration); Forge's automatic ``reply_ready`` is off by default.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

from niuu.forge_mcp.credentials import effective_grants
from niuu.forge_mcp.models import ForgeApiError, ForgeMcpLimits
from niuu.forge_mcp.tools import ForgeMcpToolbox
from skuld.flock_outcomes import FlockActivityLog
from skuld.forge_mcp.forge_client import HttpForgeClient
from skuld.forge_mcp.host import BrokerForgeMcpFlock, BrokerForgeMcpHost
from skuld.forge_mcp.routes import DESCRIBE_TOOL_PATH
from skuld.forge_mcp.skill import (
    FORGE_NOTIFY_SKILL,
    materialize_claude_plugin,
    materialize_codex_skills_root,
)
from skuld.session_runtime import SessionRuntime
from skuld.transports.mcp_config import mcp_server_names, with_default_mcp_servers
from skuld.transports.session_tools import (
    BROKER_TOKEN_FILE_ENV,
    FORGE_DESCRIBE_URL_ENV,
    PRESENT_FILE_URL_ENV,
    SessionTools,
    capability_instructions,
)

STDIO_MODULE = "skuld.forge_mcp"
FROZEN_SUBCOMMAND = ("platform", "forge-mcp")
_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]", "localhost", "127.0.0.1"}
_LOOPBACK_HOST = "127.0.0.1"


def forge_mcp_command() -> list[str]:
    """How to start the stdio server with the interpreter/binary running this broker.

    ``-P`` keeps the agent's working directory off ``sys.path`` so a workspace that
    happens to contain a ``skuld`` package cannot shadow the installed one.
    """
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return [sys.executable, *FROZEN_SUBCOMMAND]
    return [sys.executable, "-P", "-m", STDIO_MODULE]


class ForgeSessionMixin:
    """Owns the per-start runtime dir, session tools and the Forge MCP toolbox."""

    _session_runtime_cache: SessionRuntime | None
    _session_tools_cache: SessionTools | None
    _forge_mcp_toolbox_cache: ForgeMcpToolbox | None
    _flock_activity: FlockActivityLog | None

    @property
    def _session_runtime(self) -> SessionRuntime:
        if self._session_runtime_cache is None:
            self._session_runtime_cache = SessionRuntime.create(
                self._settings.forge_mcp.runtime_dir, str(self.session_id)
            )
        return self._session_runtime_cache

    def _loopback_base_url(self) -> str:
        host = (self._settings.host or "").strip()
        if host in _WILDCARD_HOSTS:
            host = _LOOPBACK_HOST
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self._settings.port}"

    def _builtin_mcp_servers(self) -> list[dict[str, Any]]:
        cfg = self._settings.forge_mcp
        if not cfg.enabled:
            return []
        command = forge_mcp_command()
        return [
            {
                "name": cfg.server_name,
                "type": "stdio",
                "command": command[0],
                "args": [
                    *command[1:],
                    "--broker-url",
                    self._loopback_base_url(),
                    "--token-file",
                    str(self._session_runtime.token_file),
                    "--timeout",
                    str(cfg.broker_request_timeout_s),
                    "--max-concurrency",
                    str(cfg.stdio_max_concurrency),
                ],
                "startup_timeout_sec": cfg.startup_timeout_s,
                "tool_timeout_sec": cfg.tool_timeout_s,
                "description": "Forge: notify the user, inspect and supervise sessions.",
            }
        ]

    def _effective_mcp_servers(self) -> list[dict[str, Any]]:
        """Configured servers plus the built-ins (a configured same-name entry wins)."""
        configured = list(self._settings.mcp_servers)
        if not self._settings.forge_mcp.enabled:
            return configured
        return with_default_mcp_servers(configured, self._builtin_mcp_servers())

    def _effective_mcp_server_names(self) -> list[str]:
        return mcp_server_names(self._effective_mcp_servers())

    def _forge_mcp_server_injected(self) -> str | None:
        """The built-in server's name when it is the one engines will run."""
        cfg = self._settings.forge_mcp
        if not cfg.enabled:
            return None
        configured = {str(s.get("name") or "").strip() for s in self._settings.mcp_servers}
        if cfg.server_name in configured:
            return None  # a launch spec replaced it; its tools are not ours to describe
        return cfg.server_name

    def _session_tools(self) -> SessionTools:
        if self._session_tools_cache is not None:
            return self._session_tools_cache
        runtime = self._session_runtime
        cfg = self._settings.forge_mcp
        server = self._forge_mcp_server_injected()
        if self._flock_activity is not None and not server:
            raise RuntimeError(
                f"mesh.flock_broker needs the built-in {cfg.server_name!r} MCP server for its "
                "flock tools, but a configured MCP server of that name replaces it. Rename "
                "the configured server, or unset mesh.flock_broker"
            )
        plugin_dirs: tuple[Path, ...] = ()
        codex_roots: tuple[Path, ...] = ()
        if server and cfg.skill_enabled:
            plugin_dirs = (materialize_claude_plugin(runtime.root, server),)
            codex_roots = (materialize_codex_skills_root(runtime.root, server),)
        self._session_tools_cache = SessionTools(
            runtime_dir=runtime.root,
            env={
                PRESENT_FILE_URL_ENV: f"{self._loopback_base_url()}/api/present-file",
                BROKER_TOKEN_FILE_ENV: str(runtime.token_file),
                # forge-describe rides the Forge MCP loopback route, served only when enabled.
                **(
                    {FORGE_DESCRIBE_URL_ENV: f"{self._loopback_base_url()}{DESCRIBE_TOOL_PATH}"}
                    if cfg.enabled
                    else {}
                ),
            },
            instructions=capability_instructions(
                forge_mcp_server=server, flock_broker=self._flock_activity is not None
            ),
            loopback_authorization=runtime.authorization,
            claude_plugin_dirs=plugin_dirs,
            codex_skill_roots=codex_roots,
        )
        return self._session_tools_cache

    def _materialized_skill_names(self) -> list[str]:
        tools = self._session_tools_cache
        if tools is None or not (tools.claude_plugin_dirs or tools.codex_skill_roots):
            return []
        return [FORGE_NOTIFY_SKILL]

    async def _forge_mcp_http_client(self) -> httpx.AsyncClient:
        if not self.volundr_api_url:
            raise ForgeApiError(
                "Forge API URL is not configured for this session (volundr_api_url)", status=503
            )
        return await self._get_http_client()

    def _forge_mcp_toolbox(self) -> ForgeMcpToolbox:
        if self._forge_mcp_toolbox_cache is not None:
            return self._forge_mcp_toolbox_cache
        cfg = self._settings.forge_mcp
        client = HttpForgeClient(
            http_client=self._forge_mcp_http_client,
            token=cfg.token,
            timeout_s=cfg.forge_request_timeout_s,
        )
        self._forge_mcp_toolbox_cache = ForgeMcpToolbox(
            client=client,
            host=BrokerForgeMcpHost(self, client),
            # Display and gating only: Forge enforces the token's scopes itself.
            grants=effective_grants(cfg.token, cfg.grants),
            limits=ForgeMcpLimits(
                list_default_limit=cfg.list_default_limit,
                list_max_limit=cfg.list_max_limit,
                transcript_default_turns=cfg.transcript_default_turns,
                transcript_max_turns=cfg.transcript_max_turns,
                transcript_turn_max_chars=cfg.transcript_turn_max_chars,
                output_max_chars=cfg.output_max_chars,
            ),
            flock=(
                BrokerForgeMcpFlock(
                    self, self._flock_activity, max_wait_s=cfg.flock_outcome_wait_max_s
                )
                if self._flock_activity is not None
                else None
            ),
        )
        return self._forge_mcp_toolbox_cache

    def _revoke_session_runtime(self) -> None:
        if self._session_runtime_cache is not None:
            self._session_runtime_cache.revoke()
