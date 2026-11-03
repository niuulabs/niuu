"""Tests for the shared Claude spawn-env builder (subscription-auth default).

The deploy env injects ANTHROPIC_API_KEY for the platform; Claude sessions must
NOT inherit it by default — the spawned CLI authenticates with the host's
claude.ai login instead (API-key orgs without data retention 400 on
retention-gated models like claude-fable-5). SKULD__CLAUDE_AUTH=api_key is the
escape hatch that restores the old behavior.
"""

import os
import traceback
from unittest.mock import AsyncMock, patch

import pytest

from skuld.transports.claude_env import CLAUDE_OAUTH_TOKEN_FILE_ENV, claude_spawn_env


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
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
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


@pytest.fixture
def oauth_file(tmp_path):
    path = tmp_path / "synthetic-oauth-token"
    path.write_bytes(b"test-only-subscription-token\n")
    path.chmod(0o400)
    return path


class TestOAuthFile:
    def test_broker_keeps_pointer_and_only_claude_child_gets_token(self, oauth_file, tmp_path):
        from skuld.transports.session_env import session_process_env
        from volundr.adapters.outbound.local_process import LocalProcessPodManager
        from volundr.domain.models import PodSpecAdditions, SessionSpec

        parent = {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file), "PATH": "/usr/bin"}
        spec = SessionSpec(values={"broker": {"cliType": "claude"}}, pod_spec=PodSpecAdditions())
        with patch.dict(os.environ, parent, clear=True):
            broker = LocalProcessPodManager._build_env(spec, tmp_path)
            assert dict(os.environ) == parent
        assert broker[CLAUDE_OAUTH_TOKEN_FILE_ENV] == str(oauth_file)
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in broker
        with patch.dict(os.environ, broker, clear=True):
            assert CLAUDE_OAUTH_TOKEN_FILE_ENV not in session_process_env()
            assert claude_spawn_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-subscription-token"
            assert dict(os.environ) == broker

    def test_reads_into_child_only_and_rereads_after_rotation(self, oauth_file, caplog):
        parent = {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file), "PATH": "/usr/bin"}
        with patch.dict(os.environ, parent, clear=True):
            first = claude_spawn_env()
            assert first["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-subscription-token"
            assert CLAUDE_OAUTH_TOKEN_FILE_ENV not in first
            assert dict(os.environ) == parent
            replacement = oauth_file.with_suffix(".new")
            replacement.write_bytes(b"test-only-rotated-token\n")
            replacement.chmod(0o400)
            replacement.replace(oauth_file)
            second = claude_spawn_env()
            assert second["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-rotated-token"
            assert dict(os.environ) == parent
        assert "test-only-subscription-token" not in caplog.text
        assert "test-only-rotated-token" not in caplog.text

    @pytest.mark.parametrize("transport_name", ["subprocess", "persistent", "websocket"])
    async def test_native_inference_spawn_receives_only_child_token(
        self, oauth_file, tmp_path, transport_name
    ):
        from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
        from skuld.transports.sdk_websocket import SdkWebSocketTransport
        from skuld.transports.subprocess import SubprocessTransport

        captured = {}

        async def capture_spawn(*command, **kwargs):
            captured.update(command=command, env=kwargs["env"])
            raise RuntimeError("synthetic spawn intercepted")

        parent = {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file), "PATH": "/usr/bin"}
        with (
            patch.dict(os.environ, parent, clear=True),
            patch("asyncio.create_subprocess_exec", side_effect=capture_spawn),
        ):
            with pytest.raises(RuntimeError, match="synthetic spawn intercepted"):
                if transport_name == "subprocess":
                    await SubprocessTransport(str(tmp_path))._send_message_once("test-only")
                elif transport_name == "persistent":
                    await PersistentSubprocessTransport(str(tmp_path))._spawn()
                else:
                    await SdkWebSocketTransport(
                        str(tmp_path), sdk_port=9100, session_id="test-only"
                    )._spawn()
            assert dict(os.environ) == parent
        assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-subscription-token"
        assert CLAUDE_OAUTH_TOKEN_FILE_ENV not in captured["env"]
        assert "test-only-subscription-token" not in repr(captured["command"])

    def test_tmux_inference_environment_receives_file_token(self, oauth_file, tmp_path):
        from skuld.transports.tmux_interactive import TmuxInteractiveTransport

        parent = {
            CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file),
            "SKULD__TMUX_REMOTE_CONTROL": "0",
        }
        with patch.dict(os.environ, parent, clear=True):
            env = TmuxInteractiveTransport(str(tmp_path))._spawn_env()
            assert dict(os.environ) == parent
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-subscription-token"
        assert CLAUDE_OAUTH_TOKEN_FILE_ENV not in env

    @pytest.mark.parametrize(
        "conflict",
        [
            {"SKULD__CLAUDE_AUTH": "api_key"},
            {"SKULD__CLAUDE_AUTH": "unknown"},
            {"ANTHROPIC_API_KEY": "test-only-api-key"},
            {"ANTHROPIC_AUTH_TOKEN": "test-only-auth-token"},
            {"ANTHROPIC_BASE_URL": "https://test-only.invalid"},
            {"CLAUDE_CODE_OAUTH_TOKEN": "test-only-ambient-oauth"},
            {"CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "123"},
        ],
    )
    def test_conflicting_auth_refuses_before_read(self, oauth_file, conflict):
        parent = {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file), **conflict}
        with (
            patch.dict(os.environ, parent, clear=True),
            patch("skuld.transports.claude_env._read_oauth_token_file") as read,
        ):
            with pytest.raises(ValueError, match="requires unambiguous direct subscription auth"):
                claude_spawn_env()
            read.assert_not_called()
            assert dict(os.environ) == parent

    def test_gateway_refuses_before_read(self, oauth_file):
        with (
            patch.dict(os.environ, {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file)}, clear=True),
            patch("skuld.transports.claude_env._read_oauth_token_file") as read,
        ):
            with pytest.raises(ValueError, match="requires unambiguous direct subscription auth"):
                claude_spawn_env(gateway_url="http://test-only.invalid", gateway_token="test-only")
            read.assert_not_called()

    @pytest.mark.parametrize(
        "raw",
        [
            b"",
            b"\n",
            b"test-only\r\n",
            b"test\nonly\n",
            b"test only",
            b"test\x00only",
            b"\xff",
            b"x" * 4097,
        ],
    )
    def test_invalid_content_has_no_fallback_or_value_in_errors(self, oauth_file, raw, caplog):
        oauth_file.chmod(0o600)
        oauth_file.write_bytes(raw)
        oauth_file.chmod(0o400)
        with patch.dict(os.environ, {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file)}, clear=True):
            with pytest.raises(ValueError) as error:
                claude_spawn_env()
        assert str(error.value) == "Configured Claude OAuth token file is invalid or unreadable"
        assert str(oauth_file) not in "".join(traceback.format_exception(error.value))
        assert str(oauth_file) not in caplog.text

    @pytest.mark.parametrize(
        "kind",
        [
            "missing",
            "relative",
            "blank",
            "symlink",
            "directory",
            "fifo",
            "hardlink",
            "mode",
            "owner",
        ],
    )
    def test_invalid_file_metadata_is_refused(self, oauth_file, kind):
        path = oauth_file
        if kind == "missing":
            path = path.with_suffix(".missing")
        elif kind == "relative":
            path = "relative-token"
        elif kind == "blank":
            path = ""
        elif kind == "symlink":
            path = oauth_file.with_suffix(".link")
            path.symlink_to(oauth_file)
        elif kind == "directory":
            path = oauth_file.parent
        elif kind == "fifo":
            path = oauth_file.with_suffix(".fifo")
            os.mkfifo(path, 0o400)
        elif kind == "hardlink":
            os.link(oauth_file, oauth_file.with_suffix(".hardlink"))
        elif kind == "mode":
            path.chmod(0o600)
        with (
            patch.dict(os.environ, {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(path)}, clear=True),
            patch(
                "skuld.transports.claude_env.os.geteuid",
                return_value=os.geteuid() + (kind == "owner"),
            ),
        ):
            with pytest.raises(ValueError, match="invalid or unreadable"):
                claude_spawn_env()

    async def test_final_sdk_subprocess_selects_file_oauth_without_api_auth(
        self, oauth_file, tmp_path
    ):
        from claude_agent_sdk._errors import CLIConnectionError
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

        from skuld.transports.sdk import SDKTransport

        captured = {}

        async def capture_spawn(command, **kwargs):
            captured.update(command=command, env=kwargs["env"])
            raise RuntimeError("synthetic spawn intercepted")

        class Client:
            def __init__(self, options):
                self.options = options

            async def __aenter__(self):
                self.options.cli_path = "/test-only/claude"
                await SubprocessCLITransport(prompt="", options=self.options).connect()

        # This matches the deployed broker contract: it receives only a pointer,
        # never ambient API/AUTH/OAuth values. Exercise the SDK's real env merge.
        parent = {CLAUDE_OAUTH_TOKEN_FILE_ENV: str(oauth_file), "PATH": "/usr/bin"}
        with (
            patch.dict(os.environ, parent, clear=True),
            patch("skuld.transports.sdk.ClaudeSDKClient", Client),
            patch.object(SubprocessCLITransport, "_check_claude_version", new_callable=AsyncMock),
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.anyio.open_process",
                side_effect=capture_spawn,
            ),
        ):
            with pytest.raises(CLIConnectionError, match="synthetic spawn intercepted"):
                await SDKTransport(workspace_dir=str(tmp_path))._connect_client()
            assert dict(os.environ) == parent
        assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-only-subscription-token"
        assert "ANTHROPIC_API_KEY" not in captured["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
        assert "FORGE_EXP_CAPTURE_SECRET" not in captured["env"]
        assert "test-only-subscription-token" not in repr(captured["command"])
        # The SDK re-inherits this nonsecret pointer; it never becomes a global token.
        assert captured["env"][CLAUDE_OAUTH_TOKEN_FILE_ENV] == str(oauth_file)
