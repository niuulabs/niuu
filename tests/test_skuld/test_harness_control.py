"""A member of a workflow run's unit stops or restarts its own harness when Forge asks."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from niuu.domain.services.forge_session_token import LAUNCH_ID_CLAIM
from niuu.room_access import required_role_for_route
from skuld import broker_api as api
from skuld.harness_control import HarnessControl


def _token(tmp_path, launch_id: str):
    path = tmp_path / "token"
    path.write_text(jwt.encode({LAUNCH_ID_CLAIM: launch_id}, "k" * 32, algorithm="HS256"))
    return str(path)


def _broker(*, member: bool = True, token_file: str = ""):
    return SimpleNamespace(
        _settings=SimpleNamespace(
            workflow_steps=SimpleNamespace(enabled=member),
            forge_mcp=SimpleNamespace(token_file=token_file),
        ),
        shutdown=AsyncMock(),
    )


def _control(broker, *, server: object | None = None) -> HarnessControl:
    control = HarnessControl(lambda: broker)
    if server is not None:
        control.attach(server)
    return control


async def test_stop_ends_the_brokers_work_and_leaves_the_process_up():
    broker = _broker()
    server = SimpleNamespace(should_exit=False)
    control = _control(broker, server=server)

    await control.stop()

    broker.shutdown.assert_awaited_once()
    assert server.should_exit is False
    assert control.restart_requested is False


async def test_restart_stops_then_asks_the_process_to_be_replaced(tmp_path):
    broker = _broker(token_file=_token(tmp_path, "launch-2"))
    server = SimpleNamespace(should_exit=False)
    control = _control(broker, server=server)

    await control.restart("launch-2")

    broker.shutdown.assert_awaited_once()
    assert server.should_exit is True
    assert control.restart_requested is True


async def test_restart_is_refused_until_the_credential_file_holds_the_new_launch(tmp_path):
    broker = _broker(token_file=_token(tmp_path, "launch-1"))
    server = SimpleNamespace(should_exit=False)
    control = _control(broker, server=server)

    with pytest.raises(api.HarnessControlError) as stale:
        await control.restart("launch-2")

    assert (stale.value.status, stale.value.code) == (409, "credential_stale")
    assert "launch-1" in str(stale.value)
    broker.shutdown.assert_not_awaited()
    assert control.restart_requested is False


@pytest.mark.parametrize(
    ("token_file", "code"),
    [("", "no_token_file"), ("/nonexistent/forge-token", "credential_unreadable")],
)
async def test_restart_needs_a_credential_file_it_can_read(token_file, code):
    control = _control(_broker(token_file=token_file), server=SimpleNamespace(should_exit=False))

    with pytest.raises(api.HarnessControlError) as refused:
        await control.restart("launch-2")

    assert (refused.value.status, refused.value.code) == (409, code)


async def test_neither_is_done_for_a_session_that_is_not_a_member(tmp_path):
    broker = _broker(member=False, token_file=_token(tmp_path, "launch-2"))
    control = _control(broker, server=SimpleNamespace(should_exit=False))

    for call in (control.stop(), control.restart("launch-2")):
        with pytest.raises(api.HarnessControlError) as refused:
            await call
        assert (refused.value.status, refused.value.code) == (409, "not_hosted_member")
    broker.shutdown.assert_not_awaited()


async def test_a_process_that_cannot_replace_itself_refuses_a_restart(tmp_path):
    control = _control(_broker(token_file=_token(tmp_path, "launch-2")))

    with pytest.raises(api.HarnessControlError) as refused:
        await control.restart("launch-2")

    assert (refused.value.status, refused.value.code) == (503, "not_restartable")


def test_the_new_process_is_the_same_command(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    monkeypatch.setattr(sys, "orig_argv", ["python3", "-m", "skuld", "--flag"])

    HarnessControl(lambda: None).reexec()

    assert calls == [(sys.executable, [sys.executable, "-m", "skuld", "--flag"])]


class TestRoutes:
    async def test_stop_answers_stopped(self, monkeypatch):
        broker = _broker()
        monkeypatch.setattr(api, "harness_control", _control(broker))

        assert await api.stop_harness() == {"status": "stopped"}

    async def test_restart_answers_restarting(self, monkeypatch, tmp_path):
        broker = _broker(token_file=_token(tmp_path, "launch-2"))
        monkeypatch.setattr(
            api, "harness_control", _control(broker, server=SimpleNamespace(should_exit=False))
        )

        answer = await api.restart_harness(api._HarnessRestartRequest(launch_id="launch-2"))

        assert answer == {"status": "restarting"}

    async def test_a_refusal_carries_its_code_and_status(self, monkeypatch, tmp_path):
        broker = _broker(token_file=_token(tmp_path, "launch-1"))
        monkeypatch.setattr(
            api, "harness_control", _control(broker, server=SimpleNamespace(should_exit=False))
        )

        with pytest.raises(HTTPException) as refused:
            await api.restart_harness(api._HarnessRestartRequest(launch_id="launch-2"))

        assert refused.value.status_code == 409
        assert refused.value.detail["error"] == "credential_stale"

    async def test_a_refused_stop_carries_its_status(self, monkeypatch):
        monkeypatch.setattr(api, "harness_control", _control(_broker(member=False)))

        with pytest.raises(HTTPException) as refused:
            await api.stop_harness()

        assert refused.value.status_code == 409

    def test_the_routes_are_owner_only(self):
        assert required_role_for_route("POST", "harness/stop") == "owner"
        assert required_role_for_route("POST", "harness/restart") == "owner"
        assert {"/api/harness/stop", "/api/harness/restart"} <= set(api.app.openapi()["paths"])


class TestMain:
    """``skuld.broker.main`` hands the server to the control and re-executes after a restart."""

    @staticmethod
    def _patch(monkeypatch, *, restart: bool):
        import uvicorn

        from skuld import broker as broker_module

        control = HarnessControl(lambda: None)
        reexec = []
        control.reexec = lambda: reexec.append(True)
        monkeypatch.setattr(api, "harness_control", control)
        monkeypatch.setattr(
            broker_module, "SkuldSettings", lambda: SimpleNamespace(host="127.0.0.1", port=9)
        )

        class Server:
            def __init__(self, config):
                self.config = config

            def run(self):
                control.restart_requested = restart

        monkeypatch.setattr(uvicorn, "Server", Server)
        return broker_module, control, reexec

    def test_the_process_is_replaced_after_a_requested_restart(self, monkeypatch):
        broker_module, control, reexec = self._patch(monkeypatch, restart=True)

        broker_module.main()

        assert control._server.config.port == 9
        assert reexec == [True]

    def test_an_ordinary_exit_is_not_replaced(self, monkeypatch):
        broker_module, _, reexec = self._patch(monkeypatch, restart=False)

        broker_module.main()

        assert reexec == []
