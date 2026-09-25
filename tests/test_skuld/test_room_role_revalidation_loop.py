"""WebSocketLifecycleMixin's 'remote'-mode periodic revalidation loop.

Mirrors niuu.session_proxy._revalidate_loop: a grant revoked or demoted
after connect must close an already-open socket within one interval, but a
merely TRANSIENT resolution failure (a Forge blip) must not tear down an
otherwise-healthy connection — only an explicit "no longer allowed" answer
does that. An UNEXPECTED failure of the loop itself still fails closed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skuld.room_role_port import RoomRoleResolutionError
from skuld.websocket_lifecycle import WebSocketLifecycleMixin


def _fake_self(resolve_room_role, *, interval: float = 5.0):
    return SimpleNamespace(
        _resolve_room_role=resolve_room_role,
        _settings=SimpleNamespace(
            ws_auth=SimpleNamespace(room_role_revalidate_interval_seconds=interval)
        ),
    )


class TestRevalidateRemoteRoomRole:
    async def test_same_role_is_still_allowed(self):
        fake_self = _fake_self(AsyncMock(return_value="viewer"))
        allowed = await WebSocketLifecycleMixin._revalidate_remote_room_role(
            fake_self, websocket=object(), original_role="viewer"
        )
        assert allowed is True

    async def test_promotion_is_still_allowed(self):
        fake_self = _fake_self(AsyncMock(return_value="owner"))
        allowed = await WebSocketLifecycleMixin._revalidate_remote_room_role(
            fake_self, websocket=object(), original_role="viewer"
        )
        assert allowed is True

    async def test_demotion_closes(self):
        fake_self = _fake_self(AsyncMock(return_value="viewer"))
        allowed = await WebSocketLifecycleMixin._revalidate_remote_room_role(
            fake_self, websocket=object(), original_role="owner"
        )
        assert allowed is False

    async def test_no_grant_closes(self):
        fake_self = _fake_self(AsyncMock(return_value=None))
        allowed = await WebSocketLifecycleMixin._revalidate_remote_room_role(
            fake_self, websocket=object(), original_role="viewer"
        )
        assert allowed is False

    async def test_transient_resolution_failure_is_still_allowed(self):
        """A momentary Forge blip must not tear down an otherwise-healthy
        connection — this is the asymmetry vs. the initial connect-time
        check, which DOES fail closed on the same error."""
        fake_self = _fake_self(AsyncMock(side_effect=RoomRoleResolutionError("blip")))
        allowed = await WebSocketLifecycleMixin._revalidate_remote_room_role(
            fake_self, websocket=object(), original_role="owner"
        )
        assert allowed is True


class TestRoomRoleRevalidationLoop:
    async def test_closes_the_socket_on_the_first_denied_revalidation(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        fake_self = SimpleNamespace(
            _settings=SimpleNamespace(
                ws_auth=SimpleNamespace(room_role_revalidate_interval_seconds=5.0)
            ),
            _revalidate_remote_room_role=AsyncMock(return_value=False),
        )
        websocket = SimpleNamespace(close=AsyncMock())
        await WebSocketLifecycleMixin._room_role_revalidation_loop(fake_self, websocket, "owner")
        websocket.close.assert_awaited_once_with(code=1008, reason="Access revoked or downgraded")

    async def test_keeps_looping_while_still_allowed(self, monkeypatch):
        sleep_calls = []

        async def _fake_sleep(interval):
            sleep_calls.append(interval)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        fake_self = SimpleNamespace(
            _settings=SimpleNamespace(
                ws_auth=SimpleNamespace(room_role_revalidate_interval_seconds=5.0)
            ),
            _revalidate_remote_room_role=AsyncMock(return_value=True),
        )
        websocket = SimpleNamespace(close=AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await WebSocketLifecycleMixin._room_role_revalidation_loop(
                fake_self, websocket, "owner"
            )
        assert len(sleep_calls) == 3
        websocket.close.assert_not_awaited()

    async def test_unexpected_failure_fails_closed(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        fake_self = SimpleNamespace(
            _settings=SimpleNamespace(
                ws_auth=SimpleNamespace(room_role_revalidate_interval_seconds=5.0)
            ),
            _revalidate_remote_room_role=AsyncMock(side_effect=RuntimeError("boom")),
        )
        websocket = SimpleNamespace(close=AsyncMock())
        await WebSocketLifecycleMixin._room_role_revalidation_loop(fake_self, websocket, "owner")
        websocket.close.assert_awaited_once_with(code=1011, reason="Revalidation failed")
