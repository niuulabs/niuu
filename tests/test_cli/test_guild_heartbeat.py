"""Tests for cli.services.guild_heartbeat.run_heartbeat_loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
async def test_a_failed_heartbeat_is_retried_next_tick_not_raised() -> None:
    fake_identity = AsyncMock()
    with (
        patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
        patch(
            "cli.services.guild_heartbeat.heartbeat",
            AsyncMock(side_effect=GuildAPIError("temporary network blip")),
        ) as heartbeat_mock,
    ):
        # Must not raise despite every call failing.
        await run_heartbeat_loop(_joined_settings(), iterations=3)

    assert heartbeat_mock.await_count == 3
