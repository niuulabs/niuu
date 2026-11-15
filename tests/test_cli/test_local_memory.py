"""Tests for mini mode's memory brain (cli.services.local_memory) and its wiring."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from cli.commands import platform as platform_module
from cli.commands.platform import (
    _build_preflight_config,
    _check_memory_env_conflicts,
    _local_memory,
    _shutdown,
    _start_local_memory,
)
from cli.config import AuthConfig, CLISettings, LocalGBrainConfig, LocalMemoryConfig
from cli.services import local_memory
from cli.services.local_memory import (
    LocalMemoryBrain,
    LocalMemoryError,
    memory_environment,
    resolve_gbrain_executable,
)

#: A stand-in for the compiled gbrain: the subcommands mini mode runs, recorded in
#: $GBRAIN_HOME/../calls.jsonl. FAKE_GBRAIN_* switches select failures.
FAKE_GBRAIN = """\
import http.server, json, os, sys, time
from pathlib import Path

home = Path(os.environ["GBRAIN_HOME"])
calls = home.parent / "calls.jsonl"
with calls.open("a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
switch = home.parent / "behaviour"
behaviour = switch.read_text().strip() if switch.exists() else ""
command = sys.argv[1]
if command == "init":
    if behaviour == "init-fails":
        print("cannot create the database", file=sys.stderr)
        sys.exit(3)
    if behaviour == "init-hangs":
        time.sleep(60)
    (home / ".gbrain").mkdir(parents=True)
    (home / ".gbrain" / "config.json").write_text("{}")
elif command == "auth":
    if behaviour == "auth-silent":
        print("Token created")
        sys.exit(0)
    print('Token created for "' + sys.argv[3] + '":\\n\\n  gbrain_faketoken123\\n\\nSave it.')
elif command == "serve":
    print("serving", flush=True)
    if behaviour == "serve-exits":
        print("database is locked", flush=True)
        sys.exit(1)
    port = int(sys.argv[sys.argv.index("--port") + 1])
    host = sys.argv[sys.argv.index("--bind") + 1]

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/health" and behaviour != "serve-unhealthy"
            self.send_response(200 if ok else 503)
            self.end_headers()

        def log_message(self, *args):
            pass

    http.server.HTTPServer((host, port), Health).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def fake_gbrain(tmp_path: Path) -> Path:
    executable = tmp_path / "bin" / "gbrain"
    executable.parent.mkdir()
    executable.write_text(f"#!{sys.executable}\n{FAKE_GBRAIN}")
    executable.chmod(0o755)
    return executable


def _config(tmp_path: Path, **gbrain: object) -> LocalMemoryConfig:
    settings = {
        "port": _free_port(),
        "health_poll_interval_seconds": 0.05,
        "startup_timeout_seconds": 10.0,
        "stop_timeout_seconds": 5.0,
        **gbrain,
    }
    return LocalMemoryConfig(home=str(tmp_path / "memory"), gbrain=LocalGBrainConfig(**settings))


def _calls(brain: LocalMemoryBrain) -> list[dict]:
    path = brain.home / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _behave(brain: LocalMemoryBrain, behaviour: str) -> None:
    brain.home.mkdir(parents=True, exist_ok=True)
    (brain.home / "behaviour").write_text(behaviour)


# ---------------------------------------------------------------------------
# Finding gbrain
# ---------------------------------------------------------------------------


class TestResolveGBrainExecutable:
    def test_configured_executable(self, fake_gbrain: Path) -> None:
        assert resolve_gbrain_executable(str(fake_gbrain)) == fake_gbrain

    def test_configured_path_that_is_not_executable_names_the_knob(self, tmp_path: Path) -> None:
        plain = tmp_path / "gbrain"
        plain.write_text("not runnable")
        with pytest.raises(LocalMemoryError, match="local_memory.gbrain.executable"):
            resolve_gbrain_executable(str(plain))

    def test_carried_executable(self, _carried_gbrain: Path) -> None:
        assert resolve_gbrain_executable("") == _carried_gbrain

    def test_dev_build_when_the_binary_carries_none(
        self, fake_gbrain: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_memory, "BUNDLED_GBRAIN_EXECUTABLE", Path("/nonexistent/x"))
        monkeypatch.setattr(local_memory, "DEV_GBRAIN_EXECUTABLE", fake_gbrain)
        assert resolve_gbrain_executable("") == fake_gbrain

    def test_none_carried_is_fatal_with_the_remedy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_memory, "BUNDLED_GBRAIN_EXECUTABLE", Path("/nonexistent/x"))
        with pytest.raises(LocalMemoryError) as raised:
            resolve_gbrain_executable("")
        message = str(raised.value)
        assert "make build-gbrain" in message
        assert "local_memory.enabled: false" in message


# ---------------------------------------------------------------------------
# The supervised process
# ---------------------------------------------------------------------------


class TestLocalMemoryBrain:
    async def test_first_start_creates_secrets_initialises_and_serves(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        await brain.start()
        try:
            assert await brain.health_check()
            for secret in (brain.admin_token_file, brain.signing_key_file):
                assert stat.S_IMODE(secret.stat().st_mode) == 0o600
                assert len(secret.read_text()) == 64
            assert brain.store_token_file.read_text() == "gbrain_faketoken123"
            assert stat.S_IMODE(brain.store_token_file.stat().st_mode) == 0o600
            assert stat.S_IMODE(brain.home.stat().st_mode) == 0o700
            assert brain.pid_file.exists()
            assert not brain.mimir_config_file.exists()  # nothing kept, nothing written

            calls = _calls(brain)
            assert [call["args"][0] for call in calls] == ["init", "auth", "serve"]
            assert calls[0]["args"] == [
                "init",
                "--pglite",
                "--non-interactive",
                "--no-embedding",
                "--db-only",
            ]
            assert calls[1]["args"][3:] == ["--scopes", "read,write"]
            serve_env = calls[2]["env"]
            assert serve_env["GBRAIN_HOME"] == str(brain.gbrain_home)
            assert serve_env["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"] == brain.admin_token_file.read_text()
            # Only gbrain's variables reach it, nothing of the platform's environment.
            assert "GBRAIN_ADMIN_BOOTSTRAP_TOKEN" not in calls[0]["env"]
            assert set(serve_env) - {"GBRAIN_HOME", "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"} <= {
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "__CF_USER_TEXT_ENCODING",
                "LC_CTYPE",
            }
        finally:
            await brain.stop()
        assert not brain.pid_file.exists()
        assert not await brain.health_check()

    async def test_restart_keeps_secrets_and_does_not_initialise_again(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        config = _config(tmp_path)
        brain = LocalMemoryBrain(config, executable=fake_gbrain)
        await brain.start()
        await brain.stop()
        secrets_before = {
            path: path.read_text()
            for path in (brain.admin_token_file, brain.signing_key_file, brain.store_token_file)
        }

        again = LocalMemoryBrain(config, executable=fake_gbrain)
        await again.start()
        await again.stop()

        assert [call["args"][0] for call in _calls(again)] == ["init", "auth", "serve", "serve"]
        assert {path: path.read_text() for path in secrets_before} == secrets_before

    async def test_a_secret_others_can_read_is_refused(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.home.mkdir(parents=True)
        brain.signing_key_file.write_text("k" * 64)
        brain.signing_key_file.chmod(0o644)
        with pytest.raises(LocalMemoryError, match="chmod 600"):
            await brain.start()
        assert _calls(brain) == []

    async def test_an_empty_secret_is_refused(self, tmp_path: Path, fake_gbrain: Path) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.home.mkdir(parents=True)
        brain.signing_key_file.write_text("")
        brain.signing_key_file.chmod(0o600)
        with pytest.raises(LocalMemoryError, match="is empty"):
            await brain.start()

    async def test_failed_init_fails_the_start_with_gbrains_error(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        _behave(brain, "init-fails")
        with pytest.raises(LocalMemoryError, match="cannot create the database"):
            await brain.start()
        assert not brain.pid_file.exists()

    async def test_a_hung_command_names_its_timeout(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(
            _config(tmp_path, command_timeout_seconds=0.5), executable=fake_gbrain
        )
        _behave(brain, "init-hangs")
        with pytest.raises(LocalMemoryError, match="command_timeout_seconds"):
            await brain.start()

    async def test_auth_create_without_a_token_is_fatal(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        _behave(brain, "auth-silent")
        with pytest.raises(LocalMemoryError, match="did not print the token"):
            await brain.start()
        assert not brain.store_token_file.exists()

    async def test_serve_that_exits_fails_the_start_with_its_log(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        _behave(brain, "serve-exits")
        with pytest.raises(LocalMemoryError, match="database is locked"):
            await brain.start()
        assert not brain.pid_file.exists()

    async def test_serve_that_never_answers_names_the_startup_timeout(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(
            _config(tmp_path, startup_timeout_seconds=0.5), executable=fake_gbrain
        )
        _behave(brain, "serve-unhealthy")
        with pytest.raises(LocalMemoryError, match="startup_timeout_seconds"):
            await brain.start()
        assert not brain.pid_file.exists()

    async def test_a_running_leftover_gbrain_is_refused(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.gbrain_home.mkdir(parents=True)
        leftover = await asyncio.create_subprocess_exec(
            str(fake_gbrain),
            "serve",
            "--http",
            "--bind",
            "127.0.0.1",
            "--port",
            str(_free_port()),
            env={**os.environ, "GBRAIN_HOME": str(brain.gbrain_home)},
            stdout=asyncio.subprocess.DEVNULL,
        )
        try:
            brain.pid_file.write_text(str(leftover.pid))
            with pytest.raises(LocalMemoryError, match=f"kill {leftover.pid}"):
                await brain.start()
        finally:
            leftover.kill()
            await leftover.wait()

    async def test_a_pid_reused_by_another_process_is_a_stale_record(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.home.mkdir(parents=True)
        brain.pid_file.write_text(str(os.getpid()))  # alive, but not gbrain
        await brain.start()
        try:
            assert brain.pid_file.read_text() != str(os.getpid())
        finally:
            await brain.stop()

    async def test_an_unreadable_pid_file_is_a_stale_record(
        self, tmp_path: Path, fake_gbrain: Path
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.home.mkdir(parents=True)
        brain.pid_file.write_text("not a pid")
        await brain.start()
        await brain.stop()
        assert not brain.pid_file.exists()

    async def test_a_stale_pid_file_is_cleared(self, tmp_path: Path, fake_gbrain: Path) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        brain.home.mkdir(parents=True)
        finished = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await finished.wait()
        brain.pid_file.write_text(str(finished.pid))
        await brain.start()
        try:
            assert brain.pid_file.read_text() != str(finished.pid)
        finally:
            await brain.stop()

    async def test_a_taken_port_names_the_knob(self, tmp_path: Path, fake_gbrain: Path) -> None:
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            brain = LocalMemoryBrain(_config(tmp_path, port=port), executable=fake_gbrain)
            with pytest.raises(LocalMemoryError, match="local_memory.gbrain.port"):
                await brain.start()

    async def test_gbrain_dying_while_running_is_logged_at_error(
        self, tmp_path: Path, fake_gbrain: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        await brain.start()
        try:
            with caplog.at_level(logging.ERROR, logger="cli.services.local_memory"):
                brain._process.kill()  # type: ignore[union-attr]
                await asyncio.wait_for(brain._watcher, timeout=5)  # type: ignore[arg-type]
            assert "exited with code" in caplog.text
            assert str(brain.log_file) in caplog.text
            assert not await brain.health_check()
        finally:
            await brain.stop()

    async def test_stop_kills_a_gbrain_that_ignores_sigterm_and_says_so(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stubborn = tmp_path / "gbrain"
        stubborn.write_text(
            f"#!{sys.executable}\n"
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n"
        )
        stubborn.chmod(0o755)
        brain = LocalMemoryBrain(_config(tmp_path, stop_timeout_seconds=0.5), executable=stubborn)
        brain._process = await asyncio.create_subprocess_exec(str(stubborn))
        await asyncio.sleep(0.3)
        with caplog.at_level(logging.ERROR, logger="cli.services.local_memory"):
            await brain.stop()
        assert "stop_timeout_seconds" in caplog.text
        assert brain._process is None

    async def test_stop_without_start_is_a_no_op(self, tmp_path: Path, fake_gbrain: Path) -> None:
        brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
        await brain.stop()
        assert not await brain.health_check()


# ---------------------------------------------------------------------------
# What Mímir and Forge are told
# ---------------------------------------------------------------------------


class TestMemoryEnvironment:
    def test_points_the_store_the_gateway_and_sessions_at_the_brain(self, tmp_path: Path) -> None:
        config = _config(tmp_path, port=3999)
        config.tenants = ["default", "acme"]
        brain = LocalMemoryBrain(config, executable=Path("/x/gbrain"))
        env = memory_environment(
            brain,
            config,
            forge_url="http://127.0.0.1:8080",
            session_gateway_url="http://127.0.0.1:8080/api/v1/mimir",
        )

        assert json.loads(env["MIMIR__BACKEND"]) == {
            "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
            "mcp_url": "http://127.0.0.1:3999/mcp",
            "api_token_file": str(brain.store_token_file),
            "query_expansion": False,
        }
        memory = json.loads(env["MIMIR__MEMORY"])
        assert memory["enabled"] is True
        assert memory["forge_base_url"] == "http://127.0.0.1:8080"
        assert memory["systems"] == [
            {
                "name": "local-brain",
                "adapter": "mimir.backends.gbrain.GBrainBackend",
                "mcp_url": "http://127.0.0.1:3999/mcp",
                "admin_token_file": str(brain.admin_token_file),
                "signing_key_file": str(brain.signing_key_file),
            }
        ]
        assert memory["tenants"] == {"default": "local-brain", "acme": "local-brain"}
        assert memory["service_auth"] == {
            "adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"
        }
        assert env["MEMORY__GATEWAY_URL"] == "http://127.0.0.1:8080/api/v1/mimir"
        assert set(env) == set(local_memory.MEMORY_ENV_KEYS)

    def test_the_settings_parse_where_they_are_read(self, tmp_path: Path) -> None:
        from mimir.config import MimirServiceConfig
        from volundr.config import Settings

        config = _config(tmp_path)
        brain = LocalMemoryBrain(config, executable=Path("/x/gbrain"))
        env = memory_environment(
            brain,
            config,
            forge_url="http://127.0.0.1:8080",
            session_gateway_url="http://127.0.0.1:8080/api/v1/mimir",
        )
        with patch.dict(os.environ, env):
            mimir = MimirServiceConfig()
            forge = Settings()
        assert mimir.backend["api_token_file"] == str(brain.store_token_file)
        assert mimir.memory.enabled
        assert mimir.memory.tenants == {"default": "local-brain"}
        assert forge.memory.gateway_url == "http://127.0.0.1:8080/api/v1/mimir"


# ---------------------------------------------------------------------------
# niuu platform up
# ---------------------------------------------------------------------------


class TestPlatformWiring:
    def test_local_memory_points_mimir_and_forge_at_this_host(self) -> None:
        settings = CLISettings(mode="mini", server={"port": 8123}, hosts={"enabled": False})
        brain, env = _local_memory(settings)
        assert isinstance(brain, LocalMemoryBrain)
        assert brain._mimir_settings == {
            "backend": json.loads(env["MIMIR__BACKEND"]),
            "memory": json.loads(env["MIMIR__MEMORY"]),
        }
        assert json.loads(env["MIMIR__MEMORY"])["forge_base_url"] == "http://127.0.0.1:8123"
        assert env["MEMORY__GATEWAY_URL"] == "http://127.0.0.1:8123/api/v1/mimir"

    def test_local_memory_refuses_a_host_that_verifies_callers(self) -> None:
        settings = CLISettings(mode="mini", hosts={"enabled": False})
        settings.host_auth = AuthConfig.model_construct(mode="oidc")
        with pytest.raises(typer.BadParameter, match="local_memory.enabled: false"):
            _local_memory(settings)

    def test_local_memory_without_gbrain_is_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_memory, "BUNDLED_GBRAIN_EXECUTABLE", Path("/nonexistent/x"))
        settings = CLISettings(mode="mini", hosts={"enabled": False})
        with pytest.raises(typer.BadParameter, match="make build-gbrain"):
            _local_memory(settings)

    def test_an_operators_own_memory_setting_is_refused(self) -> None:
        with patch.dict(os.environ, {"MIMIR__BACKEND": '{"adapter": "x"}'}):
            with pytest.raises(typer.BadParameter, match="local_memory.enabled: false"):
                _check_memory_env_conflicts({"MIMIR__BACKEND": '{"adapter": "y"}'})
            _check_memory_env_conflicts({"MIMIR__BACKEND": '{"adapter": "x"}'})

    def test_preflight_checks_the_gbrain_port(self) -> None:
        settings = CLISettings(mode="mini", local_memory={"gbrain": {"port": 3777}})
        assert 3777 in _build_preflight_config(settings).ports
        off = CLISettings(mode="mini", local_memory={"enabled": False, "gbrain": {"port": 3777}})
        assert 3777 not in _build_preflight_config(off).ports

    def test_up_without_memory_sets_none_of_it(self) -> None:
        service_defs = {"volundr": MagicMock(default_enabled=True)}
        settings = CLISettings(
            mode="mini", hosts={"enabled": False}, local_memory={"enabled": False}
        )
        captured: dict = {}

        async def capture(*_args, **kwargs) -> None:
            captured.update(os.environ)
            captured["memory_brain"] = kwargs["memory_brain"]

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        up_fn = platform_module._build_up_callback(service_defs, MagicMock(), settings)
        with (
            patch("cli.commands.platform._startup", AsyncMock(side_effect=capture)),
            patch("cli.commands.platform._shutdown", AsyncMock()),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch.dict(os.environ, {}, clear=True),
        ):
            up_fn(skip_preflight=True, all=False, no_web=False, host_profile="full", mounts="")
        assert captured["memory_brain"] is None
        assert not set(local_memory.MEMORY_ENV_KEYS) & set(captured)

    def test_up_with_memory_hands_the_brain_to_startup(self) -> None:
        service_defs = {"volundr": MagicMock(default_enabled=True)}
        settings = CLISettings(mode="mini", hosts={"enabled": False})
        captured: dict = {}

        async def capture(*_args, **kwargs) -> None:
            captured.update(os.environ)
            captured["memory_brain"] = kwargs["memory_brain"]

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        up_fn = platform_module._build_up_callback(service_defs, MagicMock(), settings)
        with (
            patch("cli.commands.platform._startup", AsyncMock(side_effect=capture)),
            patch("cli.commands.platform._shutdown", AsyncMock()),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch.dict(os.environ, {}, clear=True),
        ):
            up_fn(skip_preflight=True, all=False, no_web=False, host_profile="full", mounts="")
        assert isinstance(captured["memory_brain"], LocalMemoryBrain)
        assert set(local_memory.MEMORY_ENV_KEYS) <= set(captured)

    def test_a_failed_startup_stops_gbrain(self) -> None:
        service_defs = {"volundr": MagicMock(default_enabled=True)}
        settings = CLISettings(mode="mini", hosts={"enabled": False})
        manager = MagicMock()
        brain = MagicMock(stop=AsyncMock())

        async def fail(*_args, **_kwargs) -> None:
            manager._memory_brain = brain
            raise typer.Exit(1)

        up_fn = platform_module._build_up_callback(service_defs, manager, settings)
        with (
            patch("cli.commands.platform._startup", AsyncMock(side_effect=fail)),
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(typer.Exit),
        ):
            up_fn(skip_preflight=True, all=False, no_web=False, host_profile="full", mounts="")
        brain.stop.assert_awaited_once()
        assert manager._memory_brain is None

    async def test_start_failure_stops_the_managed_services_and_exits(self) -> None:
        manager = MagicMock(stop_all=AsyncMock())
        brain = MagicMock(start=AsyncMock(side_effect=LocalMemoryError("no gbrain")))
        with pytest.raises(typer.Exit):
            await _start_local_memory(manager, brain)
        manager.stop_all.assert_awaited_once()
        assert manager._memory_brain is None

    async def test_shutdown_stops_gbrain_after_the_root_server(self) -> None:
        order: list[str] = []
        root = MagicMock(stop=AsyncMock(side_effect=lambda: order.append("root")))
        brain = MagicMock(stop=AsyncMock(side_effect=lambda: order.append("gbrain")))
        manager = MagicMock(stop_all=AsyncMock(side_effect=lambda: order.append("services")))
        manager._root_server = root
        manager._memory_brain = brain
        await _shutdown(manager)
        assert order == ["root", "gbrain", "services"]


class TestSigterm:
    def test_sigterm_shuts_the_stack_down_like_ctrl_c(self) -> None:
        """A service manager's stop must stop gbrain too, not orphan it."""
        import signal

        service_defs = {"volundr": MagicMock(default_enabled=True)}
        settings = CLISettings(mode="mini", hosts={"enabled": False})
        manager = MagicMock()

        async def started(*_args, **_kwargs) -> None:
            # uvicorn raises a captured SIGTERM again when it exits: two arrive.
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
            loop.call_later(0.06, os.kill, os.getpid(), signal.SIGTERM)

        shutdown = AsyncMock()
        up_fn = platform_module._build_up_callback(service_defs, manager, settings)
        with (
            patch("cli.commands.platform._startup", AsyncMock(side_effect=started)),
            patch("cli.commands.platform._shutdown", shutdown),
            patch.dict(os.environ, {}, clear=True),
        ):
            up_fn(skip_preflight=True, all=False, no_web=False, host_profile="full", mounts="")
        shutdown.assert_awaited_once_with(manager)


async def test_start_writes_mimirs_settings_for_the_operators_commands(
    tmp_path: Path, fake_gbrain: Path
) -> None:
    from pydantic_settings import SettingsConfigDict

    from mimir.config import MimirServiceConfig

    config = _config(tmp_path)
    brain = LocalMemoryBrain(config, executable=fake_gbrain)
    env = memory_environment(
        brain, config, forge_url="http://127.0.0.1:1", session_gateway_url="http://x/api/v1/mimir"
    )
    brain.keep_mimir_settings(env)
    await brain.start()
    await brain.stop()

    class FromTheFile(MimirServiceConfig):
        """MimirServiceConfig as MIMIR_CONFIG=<the file> reads it."""

        model_config = SettingsConfigDict(
            **{**MimirServiceConfig.model_config, "yaml_file": [str(brain.mimir_config_file)]}
        )

    with patch.dict(os.environ, {}, clear=True):
        read = FromTheFile()
    assert read.backend == json.loads(env["MIMIR__BACKEND"])
    assert read.memory.enabled
    assert read.memory.systems == json.loads(env["MIMIR__MEMORY"])["systems"]


def test_stop_from_a_later_event_loop_stops_gbrain(tmp_path: Path, fake_gbrain: Path) -> None:
    """After Ctrl+C the shutdown runs in a new event loop; gbrain must still stop."""
    import gc
    import warnings

    brain = LocalMemoryBrain(_config(tmp_path), executable=fake_gbrain)
    asyncio.run(brain.start())
    pid = int(brain.pid_file.read_text())
    asyncio.run(brain.stop())
    assert not brain.pid_file.exists()
    assert str(fake_gbrain) not in local_memory._command_line(pid)
    # The first loop's handle of the stopped process can only be dropped, not closed:
    # collect it here rather than in whichever test runs next.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()


async def test_a_stubborn_gbrain_signalled_by_pid_is_killed_and_said_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import subprocess

    stubborn = tmp_path / "gbrain"
    stubborn.write_text(
        f"#!{sys.executable}\n"
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    stubborn.chmod(0o755)
    brain = LocalMemoryBrain(_config(tmp_path, stop_timeout_seconds=0.5), executable=stubborn)
    process = subprocess.Popen([str(stubborn)])
    try:
        await asyncio.sleep(0.3)
        with caplog.at_level(logging.ERROR, logger="cli.services.local_memory"):
            await brain._terminate_pid(process.pid)
        assert process.wait(timeout=5) == -9
        assert "stop_timeout_seconds" in caplog.text
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
