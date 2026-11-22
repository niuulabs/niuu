"""Tests for the devrunner entrypoint, which hosts the session terminal."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import signal
import sys
import types
from pathlib import Path

import pytest

DEVRUNNER_DIR = Path(__file__).parents[1] / "containers" / "devrunner"


def _load_devrunner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "devrunner_under_test", DEVRUNNER_DIR / "devrunner.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeTerminalServer:
    instances: list[_FakeTerminalServer] = []

    def __init__(self, *, port: int, workspace_dir: str) -> None:
        self.port = port
        self.workspace_dir = workspace_dir
        self.started = asyncio.Event()
        self.stopped = False
        self.instances.append(self)

    async def start(self) -> None:
        # The terminal starts every window in the workspace.
        assert Path(self.workspace_dir).is_dir()
        self.started.set()

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def terminal_server(monkeypatch):
    module = types.ModuleType("terminal")
    module.TerminalServer = _FakeTerminalServer
    monkeypatch.setitem(sys.modules, "terminal", module)
    monkeypatch.setattr(_FakeTerminalServer, "instances", [])
    return _FakeTerminalServer


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_serves_the_terminal_until_signalled(tmp_path, monkeypatch, terminal_server, sig):
    monkeypatch.setenv("TERMINAL_PORT", "17681")
    workspace = tmp_path / "session" / "workspace"
    runner = asyncio.create_task(_load_devrunner().Devrunner(workspace_dir=workspace).run())
    try:
        while not terminal_server.instances:
            await asyncio.sleep(0)
        terminal = terminal_server.instances[0]
        await terminal.started.wait()
        assert terminal.port == 17681
        assert terminal.workspace_dir == str(workspace)
        assert not runner.done()

        os.kill(os.getpid(), sig)
        await runner
    finally:
        loop = asyncio.get_running_loop()
        for handled in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(handled)
        runner.cancel()

    assert terminal.stopped


async def test_leaves_existing_services_data_untouched(tmp_path, monkeypatch, terminal_server):
    """Data a session kept from the removed service runner stays where it is."""
    kept = tmp_path / ".services" / "pgdata" / "PG_VERSION"
    kept.parent.mkdir(parents=True)
    kept.write_text("16\n")
    runner = asyncio.create_task(_load_devrunner().Devrunner(workspace_dir=tmp_path).run())
    try:
        while not terminal_server.instances:
            await asyncio.sleep(0)
        await terminal_server.instances[0].started.wait()
        os.kill(os.getpid(), signal.SIGTERM)
        await runner
    finally:
        loop = asyncio.get_running_loop()
        for handled in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(handled)
        runner.cancel()

    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] == [
        ".services/pgdata/PG_VERSION"
    ]
    assert kept.read_text() == "16\n"
