"""Tests for the shared Claude spawn-env builder (subscription-auth default).

The deploy env injects ANTHROPIC_API_KEY for the platform; Claude sessions must
NOT inherit it by default — the spawned CLI authenticates with the host's
claude.ai login instead (API-key orgs without data retention 400 on
retention-gated models like claude-fable-5). SKULD__CLAUDE_AUTH=api_key is the
escape hatch that restores the old behavior.
"""

from unittest.mock import patch

import pytest

from skuld.transports.claude_env import claude_spawn_env


class TestClaudeSpawnEnv:
    def test_enrolled_token_reaches_claude_without_host_credentials(self, caplog):
        with (
            patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-only-token"}, clear=True),
            patch("sys.platform", "linux"),
            patch("pathlib.Path.exists", return_value=False),
        ):
            env = claude_spawn_env()
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-token"
        assert "missing" not in caplog.text

    def test_subscription_default_strips_api_key_vars(self):
        fake = {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-test",
            "ANTHROPIC_AUTH_TOKEN": "tok",
            "CLAUDECODE": "1",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = claude_spawn_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "CLAUDECODE" not in env
        assert env["PATH"] == "/usr/bin"

    def test_api_key_mode_keeps_key_but_still_drops_claudecode(self):
        fake = {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-test",
            "CLAUDECODE": "1",
            "SKULD__CLAUDE_AUTH": "api_key",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = claude_spawn_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert "CLAUDECODE" not in env

    def test_mode_is_case_insensitive_and_trimmed(self):
        fake = {
            "ANTHROPIC_API_KEY": "sk-test",
            "SKULD__CLAUDE_AUTH": "  API_KEY  ",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = claude_spawn_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"

    def test_unknown_mode_falls_back_to_subscription(self):
        fake = {
            "ANTHROPIC_API_KEY": "sk-test",
            "SKULD__CLAUDE_AUTH": "whatever",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = claude_spawn_env()
        assert "ANTHROPIC_API_KEY" not in env


class TestModelGateway:
    def test_gateway_routes_the_cli_and_drops_the_platform_key(self):
        fake = {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-test",
            "CLAUDE_CODE_OAUTH_TOKEN": "tok",
            "CLAUDECODE": "1",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = claude_spawn_env(
                gateway_url="http://niuu:8080/api/v1/bifrost/", gateway_token="niuu-gateway"
            )
        assert env["ANTHROPIC_BASE_URL"] == "http://niuu:8080/api/v1/bifrost"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "niuu-gateway"
        assert "ANTHROPIC_API_KEY" not in env
        assert "CLAUDECODE" not in env
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
        assert env["PATH"] == "/usr/bin"

    def test_blank_gateway_means_the_vendor_api(self):
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            env = claude_spawn_env(gateway_url="  ", gateway_token="x")
        assert "ANTHROPIC_BASE_URL" not in env

    def test_blank_token_with_a_gateway_url_raises(self):
        """An empty ANTHROPIC_AUTH_TOKEN reads as 'not logged in' in a

        container, or falls back to the host's real subscription OAuth
        token being sent to the gateway on a host with a stored login —
        never a silent, unauthenticated session.
        """
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            with pytest.raises(ValueError, match="gateway_token is blank"):
                claude_spawn_env(gateway_url="http://niuu:8080/api/v1/bifrost", gateway_token="")

    def test_whitespace_only_token_with_a_gateway_url_raises(self):
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            with pytest.raises(ValueError, match="gateway_token is blank"):
                claude_spawn_env(gateway_url="http://niuu:8080/api/v1/bifrost", gateway_token="   ")
