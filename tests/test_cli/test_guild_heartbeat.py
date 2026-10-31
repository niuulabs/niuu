"""Tests for cli.services.guild_heartbeat.run_heartbeat_loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from cli.api.guild import GuildAPIError
from cli.config import CLISettings
from cli.services.guild_heartbeat import NotJoinedError, run_heartbeat_loop


def _joined_settings() -> CLISettings:
    settings = CLISettings()
    settings.guild.url = "https://guild.example.com"
    settings.guild.node_id = "node-123"
    settings.guild.heartbeat_interval_seconds = 0.001
    settings.server.external_host = "spark-1.lan"
    return settings


@pytest.mark.asyncio
async def test_raises_when_this_host_never_joined() -> None:
    with pytest.raises(NotJoinedError):
        await run_heartbeat_loop(CLISettings(), iterations=1)


@pytest.mark.asyncio
async def test_sends_one_heartbeat_and_stops_when_bounded() -> None:
    fake_identity = AsyncMock()
    with (
        patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
        patch(
            "cli.services.guild_heartbeat.heartbeat",
            AsyncMock(return_value={"ok": True, "instances": []}),
        ) as heartbeat_mock,
    ):
        await run_heartbeat_loop(_joined_settings(), iterations=1)

    heartbeat_mock.assert_awaited_once()
    assert heartbeat_mock.await_args.kwargs["node_id"] == "node-123"


@pytest.mark.asyncio
async def test_a_network_error_is_retried_next_tick_not_raised() -> None:
    fake_identity = AsyncMock()
    with (
        patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
        patch(
            "cli.services.guild_heartbeat.heartbeat",
            AsyncMock(side_effect=httpx.ConnectError("connection refused")),
        ) as heartbeat_mock,
    ):
        # Must not raise despite every call failing on a network error.
        await run_heartbeat_loop(_joined_settings(), iterations=3)

    assert heartbeat_mock.await_count == 3


@pytest.mark.asyncio
async def test_a_guild_rejection_exits_the_loop_loudly_instead_of_retrying() -> None:
    """A 401/403/404 likely means this node was revoked — retrying forever
    against a Guild that will keep saying no is the wrong behavior."""
    fake_identity = AsyncMock()
    with (
        patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
        patch(
            "cli.services.guild_heartbeat.heartbeat",
            AsyncMock(side_effect=GuildAPIError("Guild returned 401: revoked", status_code=401)),
        ) as heartbeat_mock,
    ):
        with pytest.raises(GuildAPIError):
            await run_heartbeat_loop(_joined_settings(), iterations=3)

    # Exited on the first rejection -- never retried.
    assert heartbeat_mock.await_count == 1


VOLUNDR_INSTANCE = {
    "id": "inst-1",
    "kind": "volundr",
    "slug": "spark-1",
    "name": "spark-1",
    "baseUrl": "https://spark-1.lan:8080",
    "visibility": "user",
}


@pytest.mark.asyncio
async def test_records_this_hosts_guild_instance_once(tmp_path, monkeypatch) -> None:
    """A host joined before `niuu join` recorded its instance gets it here; an
    unchanged instance is not rewritten on every tick."""
    import yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump({"guild": {"url": "https://guild.example.com", "node_id": "node-123"}})
    )
    monkeypatch.setenv("NIUU_CONFIG", str(config_file))
    fake_identity = AsyncMock()
    with (
        patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
        patch(
            "cli.services.guild_heartbeat.heartbeat",
            AsyncMock(return_value={"ok": True, "instances": [VOLUNDR_INSTANCE]}),
        ),
        patch("cli.services.guild_heartbeat.persist_guild_instance") as persist_mock,
    ):
        await run_heartbeat_loop(_joined_settings(), iterations=3)

    persist_mock.assert_called_once_with(
        {"instance_id": "inst-1", "instance_name": "spark-1", "instance_slug": "spark-1"}
    )


def test_persist_guild_instance_keeps_the_join_state(tmp_path) -> None:
    import yaml

    from cli.config import persist_guild_instance

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "guild": {"url": "https://guild.example.com", "node_id": "node-123"},
                "server": {"external_host": "spark-1.lan"},
            }
        )
    )

    persist_guild_instance(
        {"instance_id": "inst-1", "instance_name": "spark-1", "instance_slug": "spark-1"},
        config_file=config_file,
    )

    saved = yaml.safe_load(config_file.read_text())
    assert saved["guild"] == {
        "url": "https://guild.example.com",
        "node_id": "node-123",
        "instance_id": "inst-1",
        "instance_name": "spark-1",
        "instance_slug": "spark-1",
    }
    assert saved["server"] == {"external_host": "spark-1.lan"}
