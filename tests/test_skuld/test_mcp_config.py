"""Translator matrix: one MCP server list → Claude JSON, Agent SDK dict, Codex ``-c`` pairs."""

from __future__ import annotations

import json
import tomllib

import pytest

from skuld.transports.mcp_config import (
    build_acp_mcp_servers,
    build_claude_mcp_config,
    build_claude_mcp_payload,
    build_codex_mcp_overrides,
    build_sdk_mcp_servers,
    mcp_server_names,
    normalize_mcp_servers,
    toml_value,
    url_transport,
    with_default_mcp_servers,
)

STDIO = {
    "name": "forge",
    "type": "stdio",
    "command": "/venv/bin/python",
    "args": ["-m", "skuld.forge_mcp", "--broker-url", "http://127.0.0.1:9"],
    "env": {"PYTHONUNBUFFERED": "1"},
    "cwd": "/work",
    "startup_timeout_sec": 20,
}
HTTP_BEARER = {
    "name": "mimir",
    "type": "http",
    "url": "https://mimir.example/mcp",
    "bearer_token_env_var": "MIMIR_TOKEN",
    "headers": {"X-Tenant": "acme"},
}
SSE = {"name": "legacy", "type": "sse", "url": "https://old.example/sse"}
UNTYPED_URL = {"name": "plain", "url": "https://plain.example/mcp"}


def _codex_toml(overrides: list[tuple[str, str]]) -> dict:
    """Rebuild the TOML document Codex would see from its dotted ``-c`` overrides."""
    lines = []
    for key, value in overrides:
        dotted = ".".join(f'"{part}"' for part in key.split("."))
        lines.append(f"{dotted} = {value}")
    return tomllib.loads("\n".join(lines))


class TestNormalize:
    def test_keeps_headers_and_bearer_env(self) -> None:
        [server] = normalize_mcp_servers([HTTP_BEARER])
        assert server["headers"] == {"X-Tenant": "acme"}
        assert server["bearer_token_env_var"] == "MIMIR_TOKEN"

    def test_drops_invalid_entries_and_values(self) -> None:
        servers = normalize_mcp_servers(
            [
                "nope",
                {"name": "  "},
                {"name": "x", "command": "c", "startup_timeout_sec": True, "headers": "bad"},
            ]
        )
        assert servers == [{"name": "x", "command": "c"}]

    def test_non_list_is_empty(self) -> None:
        assert normalize_mcp_servers({"name": "x"}) == []
        assert mcp_server_names([STDIO, SSE, {"bad": 1}]) == ["forge", "legacy"]


class TestUrlTransport:
    @pytest.mark.parametrize(
        ("declared", "expected"),
        [("http", "http"), ("streamable_http", "http"), ("SSE", "sse"), ("", "http")],
    )
    def test_aliases(self, declared: str, expected: str) -> None:
        assert url_transport({"type": declared}) == expected


class TestClaude:
    def test_url_servers_keep_type_and_headers(self) -> None:
        payload = json.loads(build_claude_mcp_config([HTTP_BEARER, SSE, UNTYPED_URL]) or "")
        servers = payload["mcpServers"]
        assert servers["mimir"] == {
            "type": "http",
            "url": "https://mimir.example/mcp",
            "headers": {"X-Tenant": "acme", "Authorization": "Bearer ${MIMIR_TOKEN}"},
        }
        assert servers["legacy"] == {"type": "sse", "url": "https://old.example/sse"}
        assert servers["plain"] == {"type": "http", "url": "https://plain.example/mcp"}

    def test_explicit_authorization_wins_over_bearer_env(self) -> None:
        server = {**HTTP_BEARER, "headers": {"authorization": "Bearer literal"}}
        payload = build_claude_mcp_payload([server]) or {}
        assert payload["mcpServers"]["mimir"]["headers"] == {"authorization": "Bearer literal"}

    def test_stdio_shape(self) -> None:
        payload = build_claude_mcp_payload([STDIO]) or {}
        assert payload["mcpServers"]["forge"] == {
            "type": "stdio",
            "command": "/venv/bin/python",
            "args": ["-m", "skuld.forge_mcp", "--broker-url", "http://127.0.0.1:9"],
            "env": {"PYTHONUNBUFFERED": "1"},
        }

    def test_empty(self) -> None:
        assert build_claude_mcp_config([]) is None
        assert build_sdk_mcp_servers(None) == {}


class TestSdk:
    def test_same_shape_as_claude_json(self) -> None:
        sdk = build_sdk_mcp_servers([STDIO, HTTP_BEARER, SSE])
        claude = (build_claude_mcp_payload([STDIO, HTTP_BEARER, SSE]) or {})["mcpServers"]
        assert sdk == claude
        assert sdk["mimir"]["headers"]["Authorization"] == "Bearer ${MIMIR_TOKEN}"

    def test_untyped_stdio_stays_untyped(self) -> None:
        sdk = build_sdk_mcp_servers([{"name": "linear", "command": "uvx", "args": ["x"]}])
        assert sdk == {"linear": {"command": "uvx", "args": ["x"]}}


class TestCodex:
    def test_http_server_uses_codex_keys(self) -> None:
        overrides = build_codex_mcp_overrides([HTTP_BEARER])
        assert ("mcp_servers.mimir.url", '"https://mimir.example/mcp"') in overrides
        assert ("mcp_servers.mimir.bearer_token_env_var", '"MIMIR_TOKEN"') in overrides
        assert ("mcp_servers.mimir.http_headers.X-Tenant", '"acme"') in overrides
        assert not any(key.endswith(".headers") for key, _ in overrides)
        doc = _codex_toml(overrides)
        assert doc["mcp_servers"]["mimir"] == {
            "url": "https://mimir.example/mcp",
            "bearer_token_env_var": "MIMIR_TOKEN",
            "http_headers": {"X-Tenant": "acme"},
        }

    def test_stdio_server_round_trips_as_toml(self) -> None:
        doc = _codex_toml(build_codex_mcp_overrides([STDIO]))
        assert doc["mcp_servers"]["forge"] == {
            "command": "/venv/bin/python",
            "args": ["-m", "skuld.forge_mcp", "--broker-url", "http://127.0.0.1:9"],
            "env": {"PYTHONUNBUFFERED": "1"},
            "cwd": "/work",
            "startup_timeout_sec": 20.0,
        }

    def test_values_that_json_would_break_are_valid_toml(self) -> None:
        server = {"name": "odd", "command": "c", "args": ["emoji 😀", "del\x7f", 'q"uote\\']}
        doc = _codex_toml(build_codex_mcp_overrides([server]))
        assert doc["mcp_servers"]["odd"]["args"] == ["emoji 😀", "del\x7f", 'q"uote\\']


class TestTomlValue:
    def test_scalars_and_tables(self) -> None:
        assert toml_value(True) == "true"
        assert toml_value(3) == "3"
        assert toml_value(2.5) == "2.5"
        assert toml_value({}) == "{}"
        assert tomllib.loads("x = " + toml_value({"a b": [1, "t\n"]}))["x"] == {"a b": [1, "t\n"]}

    def test_rejects_unrepresentable(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            toml_value(float("inf"))
        with pytest.raises(TypeError, match="Unsupported"):
            toml_value(object())


class TestDefaults:
    def test_configured_entry_overrides_builtin_by_name(self) -> None:
        builtin = {"name": "forge", "command": "builtin"}
        custom = {"name": "forge", "url": "https://forge.example/mcp"}
        other = {"name": "mimir", "command": "m"}
        assert with_default_mcp_servers([custom, other], [builtin]) == [custom, other]
        assert with_default_mcp_servers([other], [builtin]) == [other, builtin]


class TestAcp:
    def test_stdio_and_http_shapes(self) -> None:
        servers = build_acp_mcp_servers(
            [STDIO, HTTP_BEARER, SSE],
            env={"MIMIR_TOKEN": "tok"},
            supports_http=True,
            supports_sse=True,
        )
        assert servers == [
            {
                "name": "forge",
                "command": "/venv/bin/python",
                "args": ["-m", "skuld.forge_mcp", "--broker-url", "http://127.0.0.1:9"],
                "env": [{"name": "PYTHONUNBUFFERED", "value": "1"}],
            },
            {
                "type": "http",
                "name": "mimir",
                "url": "https://mimir.example/mcp",
                "headers": [
                    {"name": "X-Tenant", "value": "acme"},
                    {"name": "Authorization", "value": "Bearer tok"},
                ],
            },
            {"type": "sse", "name": "legacy", "url": "https://old.example/sse", "headers": []},
        ]

    def test_unsupported_transport_and_missing_bearer_fail_loudly(self) -> None:
        with pytest.raises(ValueError, match="does not advertise"):
            build_acp_mcp_servers([SSE], env={}, supports_http=True, supports_sse=False)
        with pytest.raises(ValueError, match=r"\$MIMIR_TOKEN"):
            build_acp_mcp_servers([HTTP_BEARER], env={}, supports_http=True, supports_sse=True)
