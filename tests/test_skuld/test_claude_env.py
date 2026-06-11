"""Tests for the shared Claude spawn-env builder (subscription-auth default).

The deploy env injects ANTHROPIC_API_KEY for the platform; Claude sessions must
NOT inherit it by default — the spawned CLI authenticates with the host's
claude.ai login instead (API-key orgs without data retention 400 on
retention-gated models like claude-fable-5). SKULD__CLAUDE_AUTH=api_key is the
escape hatch that restores the old behavior.
"""

from unittest.mock import patch

from skuld.transports.claude_env import claude_spawn_env


class TestClaudeSpawnEnv:
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
