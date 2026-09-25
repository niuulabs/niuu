"""WebSocketLifecycleMixin._resolve_room_role's "remote" branch.

Mirrors tests/test_skuld/test_enforce_room_role_middleware.py's
TestEffectiveRoomRoleRemoteMode for the WebSocket leg — the two must never
disagree (see websocket_lifecycle.py's own docstring), so this exercises the
identical branch shape: header override wins, then identity headers are
required before calling the adapter, then the adapter's answer (or failure)
is returned/raised as-is, never downgraded to a default role.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from skuld.room_role_port import RoomRoleResolutionError
from skuld.websocket_lifecycle import WebSocketLifecycleMixin


class _FakeResolver:
    def __init__(self, role):
        self._role = role
        self.calls: list[dict] = []

    async def resolve_role(self, *, session_id, user_id, tenant_id, roles):
        self.calls.append(
            {"session_id": session_id, "user_id": user_id, "tenant_id": tenant_id, "roles": roles}
        )
        if isinstance(self._role, Exception):
            raise self._role
        return self._role


def _fake_self(resolver):
    return SimpleNamespace(
        _VALID_ROOM_ROLES=WebSocketLifecycleMixin._VALID_ROOM_ROLES,
        _settings=SimpleNamespace(
            ws_auth=SimpleNamespace(
                room_role_source="remote",
                room_role_header="x-niuu-room-role",
                user_id_header="x-auth-user-id",
                tenant_header="x-auth-tenant",
                roles_header="x-auth-roles",
            )
        ),
        _room_role_resolver=resolver,
        session_id="sess-1",
    )


def _websocket(headers: dict[str, str]):
    return SimpleNamespace(headers=headers)


@pytest.mark.parametrize("role", ["owner", "approver", "viewer"])
async def test_resolves_the_role_the_adapter_returns(role):
    resolver = _FakeResolver(role)
    result = await WebSocketLifecycleMixin._resolve_room_role(
        _fake_self(resolver), _websocket({"x-auth-user-id": "bob", "x-auth-tenant": "acme"})
    )
    assert result == role
    assert resolver.calls == [
        {"session_id": "sess-1", "user_id": "bob", "tenant_id": "acme", "roles": []}
    ]


async def test_no_grant_is_none_not_a_default_role():
    resolver = _FakeResolver(None)
    result = await WebSocketLifecycleMixin._resolve_room_role(
        _fake_self(resolver), _websocket({"x-auth-user-id": "bob"})
    )
    assert result is None


async def test_no_verified_identity_headers_is_none_without_calling_the_adapter():
    resolver = _FakeResolver("owner")
    result = await WebSocketLifecycleMixin._resolve_room_role(_fake_self(resolver), _websocket({}))
    assert result is None
    assert resolver.calls == []


async def test_adapter_failure_raises_never_falls_back_to_a_role():
    resolver = _FakeResolver(RoomRoleResolutionError("Forge unreachable"))
    with pytest.raises(RoomRoleResolutionError, match="Forge unreachable"):
        await WebSocketLifecycleMixin._resolve_room_role(
            _fake_self(resolver), _websocket({"x-auth-user-id": "bob"})
        )


async def test_unwired_resolver_raises_instead_of_defaulting():
    with pytest.raises(RoomRoleResolutionError, match="no room_role_remote adapter"):
        await WebSocketLifecycleMixin._resolve_room_role(
            _fake_self(None), _websocket({"x-auth-user-id": "bob"})
        )


async def test_present_header_still_wins_outright():
    resolver = _FakeResolver("owner")
    result = await WebSocketLifecycleMixin._resolve_room_role(
        _fake_self(resolver), _websocket({"x-niuu-room-role": "viewer"})
    )
    assert result == "viewer"
    assert resolver.calls == []
