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
        patch("cli.services.guild_heartbeat.heartbeat", AsyncMock()) as heartbeat_mock,
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
