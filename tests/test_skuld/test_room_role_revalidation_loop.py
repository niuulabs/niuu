"""WebSocketLifecycleMixin's 'remote'-mode periodic revalidation loop.

Mirrors niuu.session_proxy._revalidate_loop: a grant revoked or demoted
after connect must close an already-open socket within one interval, but a
merely TRANSIENT resolution failure (a Forge blip) must not immediately tear
down an otherwise-healthy connection — only an explicit "no longer allowed"
answer does that outright. That grace is BOUNDED, though: repeated
transient failures (by count or elapsed staleness) still close the socket,
so an authority that never recovers cannot keep a connection trusted on
stale authorization forever. Any OTHER unexpected exception fails closed
immediately, same as before.
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

    async def test_resolution_failure_raises_the_loop_decides(self):
        """This method no longer absorbs RoomRoleResolutionError itself —
        the wrapping loop owns the bounded-tolerance decision."""
        fake_self = _fake_self(AsyncMock(side_effect=RoomRoleResolutionError("blip")))
        with pytest.raises(RoomRoleResolutionError):
            await WebSocketLifecycleMixin._revalidate_remote_room_role(
                fake_self, websocket=object(), original_role="owner"
            )


def _loop_self(revalidate, **ws_auth_overrides):
    ws_auth = {
        "room_role_revalidate_interval_seconds": 5.0,
        "room_role_revalidate_max_consecutive_failures": 3,
        "room_role_revalidate_max_staleness_seconds": 60.0,
    }
    ws_auth.update(ws_auth_overrides)
    return SimpleNamespace(
        _settings=SimpleNamespace(ws_auth=SimpleNamespace(**ws_auth)),
        _revalidate_remote_room_role=revalidate,
    )


class TestRoomRoleRevalidationLoop:
    async def test_closes_the_socket_on_the_first_denied_revalidation(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        fake_self = _loop_self(AsyncMock(return_value=False))
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
        fake_self = _loop_self(AsyncMock(return_value=True))
        websocket = SimpleNamespace(close=AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await WebSocketLifecycleMixin._room_role_revalidation_loop(
                fake_self, websocket, "owner"
            )
        assert len(sleep_calls) == 3
        websocket.close.assert_not_awaited()

    async def test_unexpected_failure_fails_closed(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        fake_self = _loop_self(AsyncMock(side_effect=RuntimeError("boom")))
        websocket = SimpleNamespace(close=AsyncMock())
        await WebSocketLifecycleMixin._room_role_revalidation_loop(fake_self, websocket, "owner")
        websocket.close.assert_awaited_once_with(code=1011, reason="Revalidation failed")

    async def test_transient_failures_below_the_threshold_keep_the_connection_open(
        self, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        # 2 failures, then a successful (allowed) revalidation, then CancelledError
        # to stop the loop — fewer than max_consecutive_failures (3), so the
        # socket must never close.
        revalidate = AsyncMock(
            side_effect=[
                RoomRoleResolutionError("blip"),
                RoomRoleResolutionError("blip"),
                True,
                asyncio.CancelledError,
            ]
        )
        fake_self = _loop_self(revalidate, room_role_revalidate_max_consecutive_failures=3)
        websocket = SimpleNamespace(close=AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await WebSocketLifecycleMixin._room_role_revalidation_loop(
                fake_self, websocket, "owner"
            )
        websocket.close.assert_not_awaited()

    async def test_closes_after_max_consecutive_failures(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        revalidate = AsyncMock(side_effect=RoomRoleResolutionError("blip"))
        fake_self = _loop_self(revalidate, room_role_revalidate_max_consecutive_failures=3)
        websocket = SimpleNamespace(close=AsyncMock())
        await WebSocketLifecycleMixin._room_role_revalidation_loop(fake_self, websocket, "owner")
        assert revalidate.await_count == 3
        websocket.close.assert_awaited_once_with(
            code=1011, reason="Room role authorization unavailable"
        )

    async def test_a_successful_revalidation_resets_the_failure_count(self, monkeypatch):
        """2 failures, then a success, then 2 more failures must NOT trip a
        threshold of 3 — the counter resets on the intervening success."""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        revalidate = AsyncMock(
            side_effect=[
                RoomRoleResolutionError("blip"),
                RoomRoleResolutionError("blip"),
                True,
                RoomRoleResolutionError("blip"),
                RoomRoleResolutionError("blip"),
                asyncio.CancelledError,
            ]
        )
        fake_self = _loop_self(revalidate, room_role_revalidate_max_consecutive_failures=3)
        websocket = SimpleNamespace(close=AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await WebSocketLifecycleMixin._room_role_revalidation_loop(
                fake_self, websocket, "owner"
            )
        websocket.close.assert_not_awaited()

    async def test_closes_after_max_staleness_even_under_the_failure_count(self, monkeypatch):
        """A generous failure-count budget must not override the time bound."""
        fake_time = [1_000_000.0]
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        import skuld.websocket_lifecycle as wl_module

        monkeypatch.setattr(wl_module.time, "monotonic", lambda: fake_time[0])

        async def _failing(*_args, **_kwargs):
            fake_time[0] += 40.0
            raise RoomRoleResolutionError("blip")

        fake_self = _loop_self(
            AsyncMock(side_effect=_failing),
            room_role_revalidate_max_consecutive_failures=100,
            room_role_revalidate_max_staleness_seconds=60.0,
        )
        websocket = SimpleNamespace(close=AsyncMock())
        await WebSocketLifecycleMixin._room_role_revalidation_loop(fake_self, websocket, "owner")
        # Failures at +40s (0s stale) and +80s (40s stale, still under 60s)
        # keep the socket open; the third, at +120s (80s stale since the
        # first), crosses the staleness bound and closes it — well before
        # the failure-count budget of 100 would ever trip.
        assert fake_self._revalidate_remote_room_role.await_count == 3
        websocket.close.assert_awaited_once_with(
            code=1011, reason="Room role authorization unavailable"
        )
