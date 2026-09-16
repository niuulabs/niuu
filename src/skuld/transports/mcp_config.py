"""Helpers for adapting session MCP configs to specific CLI transports."""

from __future__ import annotations

import json
import shlex
from typing import Any


def normalize_mcp_servers(raw_servers: object) -> list[dict[str, Any]]:
    """Return a sanitized list of MCP server config dicts."""
    if not isinstance(raw_servers, list):
        return []

    servers: list[dict[str, Any]] = []
    for raw in raw_servers:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {"name": name}
        if raw.get("type"):
            entry["type"] = str(raw["type"])
        if raw.get("command"):
            entry["command"] = str(raw["command"])
        if raw.get("url"):
            entry["url"] = str(raw["url"])
        if isinstance(raw.get("args"), list):
            entry["args"] = [str(arg) for arg in raw["args"]]
        if isinstance(raw.get("env"), dict):
            entry["env"] = {str(k): str(v) for k, v in raw["env"].items()}
        if isinstance(raw.get("headers"), dict):
            entry["headers"] = {str(k): str(v) for k, v in raw["headers"].items()}
        for key in ("credential_file", "credential_format", "auth_header", "auth_prefix"):
            if key in raw:
                entry[key] = str(raw[key])
        if raw.get("description"):
            entry["description"] = str(raw["description"])
        if raw.get("cwd"):
            entry["cwd"] = str(raw["cwd"])
        for timeout_key in ("startup_timeout_sec", "tool_timeout_sec"):
            timeout = raw.get(timeout_key)
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
                entry[timeout_key] = float(timeout)
        servers.append(entry)
    return servers


def build_claude_mcp_config(raw_servers: object) -> str | None:
    """Serialize MCP servers into Claude's ``--mcp-config`` JSON format."""
    servers = normalize_mcp_servers(raw_servers)
    if not servers:
        return None

    payload: dict[str, Any] = {"mcpServers": {}}
    for server in servers:
        name = server["name"]
        entry: dict[str, Any] = {}
        if server.get("url"):
            entry["url"] = server["url"]
            entry["type"] = server.get("type", "http")
            entry.update(_claude_auth(server))
        else:
            entry["command"] = server.get("command")
            entry["args"] = list(server.get("args") or [])
            if server.get("env"):
                entry["env"] = dict(server["env"])
        payload["mcpServers"][name] = entry
    return json.dumps(payload)


def build_sdk_mcp_servers(raw_servers: object) -> dict[str, dict[str, Any]]:
    """Translate MCP servers into ``ClaudeAgentOptions.mcp_servers`` format."""
    servers = normalize_mcp_servers(raw_servers)
    if not servers:
        return {}

    payload: dict[str, dict[str, Any]] = {}
    for server in servers:
        name = server["name"]
        if server.get("url"):
            server_type = str(server.get("type") or "sse")
            if server_type == "http":
                payload[name] = {"type": "http", "url": server["url"], **_claude_auth(server)}
                continue
            payload[name] = {"type": "sse", "url": server["url"], **_claude_auth(server)}
            continue

        entry: dict[str, Any] = {"command": server.get("command") or ""}
        if server.get("type") == "stdio":
            entry["type"] = "stdio"
        if server.get("args"):
            entry["args"] = list(server["args"])
        if server.get("env"):
            entry["env"] = dict(server["env"])
        payload[name] = entry
    return payload


def build_codex_mcp_overrides(raw_servers: object) -> list[tuple[str, str]]:
    """Translate MCP servers into ``codex -c key=value`` override pairs."""
    servers = normalize_mcp_servers(raw_servers)
    overrides: list[tuple[str, str]] = []
    for server in servers:
        base = f"mcp_servers.{server['name']}"
        if server.get("url"):
            overrides.append((f"{base}.url", json.dumps(server["url"])))
            if server.get("credential_file"):
                overrides.append(
                    (f"{base}.http_headers_helper", json.dumps(_header_helper(server)))
                )
            for key, value in server.get("headers", {}).items():
                overrides.append((f"{base}.http_headers.{json.dumps(key)}", json.dumps(value)))
        else:
            overrides.append((f"{base}.command", json.dumps(server.get("command") or "")))
            overrides.append((f"{base}.args", json.dumps(list(server.get("args") or []))))
            if server.get("env"):
                for env_key, env_value in dict(server["env"]).items():
                    overrides.append((f"{base}.env.{env_key}", json.dumps(env_value)))
            if server.get("cwd"):
                overrides.append((f"{base}.cwd", json.dumps(server["cwd"])))
        for timeout_key in ("startup_timeout_sec", "tool_timeout_sec"):
            if timeout_key in server:
                overrides.append((f"{base}.{timeout_key}", json.dumps(server[timeout_key])))
    return overrides


def _header_helper(server: dict[str, Any]) -> str:
    args = [
        "python3",
        "-m",
        "skuld.mcp_credentials",
        "--file",
        server["credential_file"],
        "--header",
        server.get("auth_header", "Authorization"),
        "--prefix",
        server.get("auth_prefix", "Bearer "),
    ]
    if server.get("credential_format") == "oauth":
        args.append("--oauth")
    return shlex.join(args)


def _claude_auth(server: dict[str, Any]) -> dict[str, Any]:
    if server.get("credential_file"):
        return {"headersHelper": _header_helper(server)}
    if server.get("headers"):
        return {"headers": server["headers"]}
    return {}
