"""The local process manager hands the session credential to the broker env only."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from skuld.transports.session_env import scrub_broker_credentials
from tests.support.forge_session import token_service
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.local_process import (
    FORGE_MCP_GRANTS_ENV,
    FORGE_MCP_TOKEN_ENV,
    LocalProcessPodManager,
)
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec


@pytest.fixture
def manager(tmp_path: Path) -> LocalProcessPodManager:
    return LocalProcessPodManager(
        workspaces_dir=str(tmp_path / "ws"),
        state_file=str(tmp_path / "state.json"),
        sdk_port_start=19_900,
    )


def _spec(session: Session, grants=(), **values) -> SessionSpec:
    credential = token_service().mint(
        session_id=session.id,
        session_name=session.name,
        owner_id="owner-1",
        tenant_id=None,
        grants=grants,
        launch_id="launch-1",
    )
    spec = SessionSpec(values=dict(values), pod_spec=PodSpecAdditions())
    spec.forge_session = credential
    return spec


async def _spawn(manager, session, spec, tmp_path, monkeypatch) -> tuple[list, dict]:
    workspace = tmp_path / "ws" / str(session.id)
    workspace.mkdir(parents=True)
    with (
        patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
        patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=MagicMock(pid=7),
        ) as spawn,
    ):
        await manager._spawn_skuld(session, spec, workspace, 9100)
    return list(spawn.call_args.args), spawn.call_args.kwargs["env"]


class TestBrokerEnv:
    def test_declares_delivery(self, manager) -> None:
        assert manager.delivers_forge_session_token is True

    async def test_token_and_grants_reach_the_broker_env_only(
        self, manager, tmp_path, monkeypatch, caplog
    ) -> None:
        caplog.set_level(logging.DEBUG)
        session = Session(id=uuid4(), name="builder", owner_id="owner-1")
        spec = _spec(session, grants=["message", "lifecycle"])
        argv, env = await _spawn(manager, session, spec, tmp_path, monkeypatch)

        token = spec.forge_session.token
        assert env[FORGE_MCP_TOKEN_ENV] == token
        assert env[FORGE_MCP_GRANTS_ENV] == "lifecycle,message"
        assert not any(token in str(arg) for arg in argv)
        assert token not in caplog.text
        state = tmp_path / "state.json"
        assert not state.exists() or token not in state.read_text()
        # ...and the broker never passes it on to the agent it spawns.
        model_env = scrub_broker_credentials(env)
        assert FORGE_MCP_TOKEN_ENV not in model_env
        assert token not in model_env.values()

    async def test_inherited_and_launch_spec_values_never_survive(
        self, manager, tmp_path, monkeypatch
    ) -> None:
        # Forge itself may run inside a Forge session, and a launch spec may set env.
        monkeypatch.setenv(FORGE_MCP_TOKEN_ENV, "inherited-token")
        monkeypatch.setenv(FORGE_MCP_GRANTS_ENV, "message,lifecycle")
        session = Session(id=uuid4(), name="plain", owner_id="owner-1")
        spec = SessionSpec(
            values={"env": {FORGE_MCP_TOKEN_ENV: "spec-token", FORGE_MCP_GRANTS_ENV: "lifecycle"}},
            pod_spec=PodSpecAdditions(),
        )
        _, env = await _spawn(manager, session, spec, tmp_path, monkeypatch)
        assert FORGE_MCP_TOKEN_ENV not in env and FORGE_MCP_GRANTS_ENV not in env

        minted = _spec(session, env={FORGE_MCP_TOKEN_ENV: "spec-token"})
        _, env = await _spawn(manager, session, minted, tmp_path / "again", monkeypatch)
        assert env[FORGE_MCP_TOKEN_ENV] == minted.forge_session.token
        assert env[FORGE_MCP_GRANTS_ENV] == ""


class TestSpecCarriesTheCredential:
    def test_brokered_credentials_keep_it(self) -> None:
        class Manager(BrokeredCredentialPodManager):
            pass

        manager = Manager()
        manager._configure_brokered_credentials()
        session = Session(id=uuid4(), name="s", owner_id="owner-1")
        spec = _spec(session)
        brokered = manager._with_brokered_credentials(spec)
        assert brokered.forge_session is spec.forge_session
        assert "codexAuth" in brokered.values["broker"]
        assert dataclasses.fields(SessionSpec)[-1].name == "forge_session"

    def test_repr_hides_the_token(self) -> None:
        session = Session(id=uuid4(), name="s", owner_id="owner-1")
        spec = _spec(session)
        assert spec.forge_session.token not in repr(spec)
