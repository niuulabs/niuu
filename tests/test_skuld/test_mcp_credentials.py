"""Runtime adapters consume rotating projected tokens, without interactive login."""

import json
import shlex

import pytest

from skuld.mcp_credentials import main, read_headers
from skuld.transports.mcp_config import (
    build_claude_mcp_config,
    build_codex_mcp_isolation_overrides,
    build_codex_mcp_overrides,
    build_sdk_mcp_servers,
)


def test_codex_mcp_isolation_disables_every_resolved_unlisted_server():
    assert build_codex_mcp_isolation_overrides(
        [
            {"name": "ravn-tools", "enabled": True},
            {"name": "user-configured", "enabled": True},
            {"name": "project-configured", "enabled": True},
        ],
        allowed_names={"ravn-tools"},
    ) == [
        ("mcp_servers.project-configured.enabled", "false"),
        ("mcp_servers.ravn-tools.enabled", "true"),
        ("mcp_servers.user-configured.enabled", "false"),
    ]


def test_codex_mcp_isolation_fails_closed_for_missing_or_unsafe_names():
    with pytest.raises(RuntimeError, match="Required Codex MCP server missing"):
        build_codex_mcp_isolation_overrides([], allowed_names={"ravn-tools"})
    with pytest.raises(RuntimeError, match="cannot be isolated safely"):
        build_codex_mcp_isolation_overrides(
            [{"name": "nested.server", "enabled": True}], allowed_names=set()
        )


def test_reads_current_atomic_token_document(tmp_path):
    token = tmp_path / "token"
    for value in ("initial", "rotated"):
        token.write_text(json.dumps({"access_token": value, "expires_at": "2099-01-01T00:00:00Z"}))
        assert read_headers(str(token), "Authorization", "Bearer ", True) == {
            "Authorization": f"Bearer {value}"
        }
    token.write_text('{"access_token":"non-expiring","expires_at":null}')
    assert read_headers(str(token), "X-Token", "", True) == {"X-Token": "non-expiring"}


@pytest.mark.parametrize(
    "content",
    [
        '{"access_token":"secret","expires_at":"2000-01-01T00:00:00Z"}',
        '{"access_token":"secret","expires_at":"2099-01-01T00:00:00"}',
        '{"access_token":"bad\\nheader"}',
        "{}",
        "invalid-json",
        '{"access_token":null}',
    ],
)
def test_helper_fails_without_leaking_secrets(tmp_path, monkeypatch, capsys, content):
    token = tmp_path / "token"
    token.write_text(content)
    monkeypatch.setattr("sys.argv", ["helper", "--file", str(token), "--oauth"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert "secret" not in result.err


def test_plain_token_and_cli_json(tmp_path, monkeypatch, capsys):
    token = tmp_path / "token"
    token.write_text("plain-token\n")
    monkeypatch.setattr("sys.argv", ["helper", "--file", str(token)])
    main()
    assert json.loads(capsys.readouterr().out) == {"Authorization": "Bearer plain-token"}


def test_both_runtimes_use_file_helper_and_preserve_shell_arguments():
    config = [
        {
            "name": "remote",
            "type": "http",
            "url": "https://mcp.example.test",
            "credential_file": "/run/secrets/a 'quoted'/token",
            "credential_format": "oauth",
            "auth_prefix": "Bearer ",
        }
    ]
    claude = json.loads(build_claude_mcp_config(config))["mcpServers"]["remote"]
    sdk = build_sdk_mcp_servers(config)["remote"]
    codex = dict(build_codex_mcp_overrides(config))
    helper = claude["headersHelper"]
    assert (
        helper
        == sdk["headersHelper"]
        == json.loads(codex["mcp_servers.remote.http_headers_helper"])
    )
    args = shlex.split(helper)
    assert args[args.index("--file") + 1] == config[0]["credential_file"]
    assert args[args.index("--prefix") + 1] == "Bearer "
    assert args[-1] == "--oauth"
    assert claude["type"] == "http"


def test_static_headers_are_preserved():
    config = [{"name": "remote", "url": "https://mcp.example.test", "headers": {"X-Key": "test"}}]
    assert json.loads(build_claude_mcp_config(config))["mcpServers"]["remote"]["headers"] == {
        "X-Key": "test"
    }
    assert ('mcp_servers.remote.http_headers."X-Key"', '"test"') in build_codex_mcp_overrides(
        config
    )


def test_openshell_opaque_environment_credential(monkeypatch, capsys):
    monkeypatch.setenv("NIUU_MCP_TEST", "opaque-provider-credential")
    monkeypatch.setattr("sys.argv", ["helper", "--env", "NIUU_MCP_TEST"])
    main()
    assert json.loads(capsys.readouterr().out) == {
        "Authorization": "Bearer opaque-provider-credential"
    }
    config = [
        {"name": "remote", "url": "https://mcp.example.test", "credential_env": "NIUU_MCP_TEST"}
    ]
    helper = json.loads(build_claude_mcp_config(config))["mcpServers"]["remote"]["headersHelper"]
    assert "--env NIUU_MCP_TEST" in helper
    assert (
        json.loads(
            dict(build_codex_mcp_overrides(config))["mcp_servers.remote.http_headers_helper"]
        )
        == helper
    )


def test_codex_stdio_explicitly_inherits_named_credentials_only():
    config = [{"name": "local", "command": "npx", "env_vars": ["LINEAR_API_KEY"]}]
    overrides = dict(build_codex_mcp_overrides(config))
    assert json.loads(overrides["mcp_servers.local.env_vars"]) == ["LINEAR_API_KEY"]
    assert "mcp_servers.local.env.LINEAR_API_KEY" not in overrides


def test_header_helper_runs_the_script_not_the_module():
    """`-m skuld.mcp_credentials` pays a ~6.6s package import per invocation.

    Codex allows its http_headers_helper 10s and opens MCP connections
    concurrently, so the module form timed out and the server was dropped.
    """
    from skuld.transports.mcp_config import build_codex_mcp_overrides

    overrides = dict(
        build_codex_mcp_overrides(
            [
                {
                    "name": "linear",
                    "type": "http",
                    "url": "https://mcp.example.invalid/mcp",
                    "credential_file": "/run/secrets/mcp/abc/token",
                    "credential_format": "oauth",
                }
            ]
        )
    )
    helper = overrides["mcp_servers.linear.http_headers_helper"]

    assert "-m" not in helper
    assert "skuld/mcp_credentials.py" in helper
    assert "--oauth" in helper
