"""The SDK transport routes Claude Code through the model gateway when told to."""

from __future__ import annotations

from unittest.mock import patch

from skuld.transports.sdk import SDKTransport


def test_spawn_env_routes_through_the_model_gateway(tmp_path):
    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        model_gateway_url="http://niuu:8080/api/v1/bifrost",
        model_gateway_token="niuu-gateway",
    )
    with patch.dict("os.environ", {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-p"}, clear=True):
        env = transport._spawn_env()
    assert env["ANTHROPIC_BASE_URL"] == "http://niuu:8080/api/v1/bifrost"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "niuu-gateway"
    assert "ANTHROPIC_API_KEY" not in env


def test_spawn_env_without_a_gateway_keeps_the_vendor_api(tmp_path):
    fake = {"PATH": "/usr/bin", "SKULD__CLAUDE_AUTH": "api_key", "ANTHROPIC_API_KEY": "sk-platform"}
    with patch.dict("os.environ", fake, clear=True):
        env = SDKTransport(workspace_dir=str(tmp_path))._spawn_env()
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-platform"
