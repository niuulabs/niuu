"""The resident's own room connection must carry platform identity.

A room hosted remotely (``wss://``) sits behind the platform's JWT gate, so the
drive loop's ``SkuldChannel`` has to authenticate. A remote room with no
platform identity configured is a startup error; a plain in-cluster ``ws://``
broker keeps working without one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ravn.config import InitiativeConfig
from ravn.domain.exceptions import ConfigurationError
from ravn.drive_loop import DriveLoop
from ravn.session_join import build_room_token_provider

_ACCESS_TOKEN = "pat-for-room-test"


def _settings(
    *,
    broker_url: str,
    platform_enabled: bool,
    access_token: str = "",
) -> MagicMock:
    settings = MagicMock()
    settings.skuld.enabled = True
    settings.skuld.broker_url = broker_url
    settings.skuld.display_name = "Runa"
    settings.skuld.reconnect_delay_seconds = 2.0
    settings.skuld.max_reconnect_attempts = 5
    settings.skuld.session_ready_timeout_seconds = 600.0
    settings.gateway.platform.enabled = platform_enabled
    settings.gateway.platform.access_token = access_token
    settings.gateway.platform.base_url = "https://yggdrasil.niuu.world"
    settings.mesh.enabled = True
    settings.mesh.own_peer_id = "runa"
    settings.budget.daily_cap_usd = 100.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    return settings


def _drive_loop(settings: MagicMock) -> DriveLoop:
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=1,
        queue_journal_path="/proc/no_such_dir/queue.json",
    )
    return DriveLoop(agent_factory=MagicMock(), config=config, settings=settings)


class TestRoomTokenProvider:
    def test_remote_room_without_platform_identity_is_a_configuration_error(self):
        settings = _settings(
            broker_url="wss://yggdrasil.niuu.world/rooms/abc/ws/ravn",
            platform_enabled=False,
        )

        with pytest.raises(ConfigurationError) as raised:
            build_room_token_provider(settings)

        message = str(raised.value)
        assert "wss://yggdrasil.niuu.world/rooms/abc/ws/ravn" in message
        assert "gateway.platform.enabled" in message

    def test_scheme_check_is_case_insensitive(self):
        settings = _settings(broker_url="WSS://rooms.example/ws/ravn", platform_enabled=False)

        with pytest.raises(ConfigurationError):
            build_room_token_provider(settings)

    def test_in_cluster_plain_ws_without_identity_needs_no_provider(self):
        settings = _settings(broker_url="ws://skuld:8081/ws/ravn", platform_enabled=False)

        assert build_room_token_provider(settings) is None

    @pytest.mark.asyncio
    async def test_remote_room_with_platform_identity_yields_the_platform_token(self):
        settings = _settings(
            broker_url="wss://yggdrasil.niuu.world/rooms/abc/ws/ravn",
            platform_enabled=True,
            access_token=_ACCESS_TOKEN,
        )

        provider = build_room_token_provider(settings)

        assert provider is not None
        assert await provider() == _ACCESS_TOKEN


class TestDriveLoopRoomAuth:
    @pytest.mark.asyncio
    async def test_room_channel_authenticates_with_platform_identity(self):
        settings = _settings(
            broker_url="wss://yggdrasil.niuu.world/rooms/abc/ws/ravn",
            platform_enabled=True,
            access_token=_ACCESS_TOKEN,
        )

        loop = _drive_loop(settings)

        channel = loop.operator_contact_channel()
        assert channel is not None
        provider = channel._auth_token_provider
        assert provider is not None
        assert await provider() == _ACCESS_TOKEN

    def test_remote_room_without_platform_identity_fails_at_startup(self):
        settings = _settings(
            broker_url="wss://yggdrasil.niuu.world/rooms/abc/ws/ravn",
            platform_enabled=False,
        )

        with pytest.raises(ConfigurationError, match="gateway.platform.enabled"):
            _drive_loop(settings)

    def test_in_cluster_ws_broker_without_identity_still_builds_unauthenticated(self):
        settings = _settings(broker_url="ws://skuld:8081/ws/ravn", platform_enabled=False)

        loop = _drive_loop(settings)

        channel = loop.operator_contact_channel()
        assert channel is not None
        assert channel._auth_token_provider is None
