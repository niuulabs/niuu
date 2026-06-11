"""Tests for the Remote Control transports.

The Claude transport launches ``claude remote-control``, scrapes the pairing
URL from the ANSI TUI, and emits a structured ``remote_control`` event; the
Codex variant fails fast until the standalone install exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from skuld.transports.remote_control import (
    _ANSI_RE,
    _URL_RE,
    CodexRemoteControlTransport,
    RemoteControlTransport,
)


class TestCommandConstruction:
    def test_name_carries_session_token(self, monkeypatch):
        monkeypatch.setenv("SKULD__SESSION__NAME", "my-session")
        transport = RemoteControlTransport("/tmp", session_id="abcdef12-3456-7890")

        cmd = transport._build_command()

        assert cmd[:2] == ["claude", "remote-control"]
        assert "--name" in cmd
        name = cmd[cmd.index("--name") + 1]
        assert name == "my-session-abcdef12-345"
        assert "--spawn" in cmd
        assert "same-dir" in cmd

    def test_permission_mode_follows_skip_permissions(self, monkeypatch):
        monkeypatch.delenv("SKULD__REMOTE_CONTROL_PERMISSION_MODE", raising=False)
        bypass = RemoteControlTransport("/tmp", skip_permissions=True)._build_command()
        default = RemoteControlTransport("/tmp", skip_permissions=False)._build_command()

        assert bypass[bypass.index("--permission-mode") + 1] == "bypassPermissions"
        assert default[default.index("--permission-mode") + 1] == "default"

    def test_permission_mode_env_override(self, monkeypatch):
        monkeypatch.setenv("SKULD__REMOTE_CONTROL_PERMISSION_MODE", "acceptEdits")
        cmd = RemoteControlTransport("/tmp", skip_permissions=True)._build_command()

        assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"

    def test_capabilities_disable_send_message(self):
        caps = RemoteControlTransport("/tmp").capabilities
        assert caps.send_message is False


class TestPairingUrlScrape:
    def test_url_regex_matches_pairing_link(self):
        line = "Open https://claude.ai/code?environment=env_AbC-123_xyz to pair"
        match = _URL_RE.search(line)
        assert match is not None
        assert match.group(0) == "https://claude.ai/code?environment=env_AbC-123_xyz"

    def test_ansi_sequences_are_stripped_before_scraping(self):
        decorated = "\x1b[1m\x1b[32mhttps://claude.ai/code?environment=env_tok\x1b[0m"
        clean = _ANSI_RE.sub("", decorated)
        assert _URL_RE.search(clean) is not None

    @pytest.mark.asyncio
    async def test_reader_emits_remote_control_event(self):
        transport = RemoteControlTransport("/tmp", session_id="tok12345")
        events: list[dict] = []

        async def collect(event: dict) -> None:
            events.append(event)

        transport.on_event(collect)

        class _Stdout:
            def __init__(self):
                self._lines = [
                    b"\x1b[2J booting remote control...\n",
                    b"pair here: https://claude.ai/code?environment=env_test_123\n",
                    b"",
                ]

            async def readline(self):
                return self._lines.pop(0)

        class _Proc:
            stdout = _Stdout()
            returncode = None

            async def wait(self):
                self.returncode = 0
                return 0

        transport._process = _Proc()
        await transport._read_output()

        assert transport.remote_control_url == ("https://claude.ai/code?environment=env_test_123")
        paired = [e for e in events if e.get("type") == "remote_control"]
        assert paired and paired[0]["url"].endswith("env_test_123")


class TestSpawnEnv:
    @pytest.mark.asyncio
    async def test_start_strips_api_key_auth_vars(self, monkeypatch):
        """Remote Control refuses to start under API-key auth — the spawn env
        must drop ANTHROPIC_API_KEY/AUTH_TOKEN so OAuth is used."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        transport = RemoteControlTransport("/tmp", session_id="tok12345")
        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs.get("env", {})

            class _P:
                returncode = None
                stdout = None
                pid = 4242

                async def wait(self):
                    return 0

            return _P()

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await transport.start()

        assert "ANTHROPIC_API_KEY" not in captured["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
        if transport._reader_task is not None:
            transport._reader_task.cancel()


class TestSweepKill:
    def test_sweep_targets_only_token_processes(self):
        transport = RemoteControlTransport("/tmp", session_id="uniquetok999")
        seen: list[int] = []

        def fake_kill(pid: int, sig: int) -> None:
            seen.append(pid)

        process_table = {
            101: "claude remote-control --name volundr-uniquetok999 --spawn same-dir",
            102: "claude remote-control --name volundr-othertoken --spawn same-dir",
            103: "claude daemon",
        }

        with (
            patch.object(transport, "_iter_processes", return_value=process_table.items()),
            patch("os.kill", side_effect=fake_kill),
        ):
            killed = transport._sweep_kill(15)

        assert killed == 1
        assert seen == [101]


class TestCodexStub:
    @pytest.mark.asyncio
    async def test_send_message_is_rejected(self):
        transport = CodexRemoteControlTransport("/tmp")
        transport._emit = AsyncMock()

        await transport.start()
        assert transport.is_alive is False

    def test_capabilities_disable_send_message(self):
        caps = CodexRemoteControlTransport("/tmp").capabilities
        assert caps.send_message is False
