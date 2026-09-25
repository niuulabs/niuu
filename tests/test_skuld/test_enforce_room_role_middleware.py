"""skuld.broker_api._enforce_room_role / _effective_room_role: the HTTP
defense-in-depth room-role gate, independent of the session proxy's own
enforcement.

A missing x-niuu-room-role header's meaning is governed entirely by
ws_auth.room_role_source (skuld.config.WsAuthConfig):

- "deployment" (the default — Kubernetes, OpenShell, VM, and any backend
  other than a proxy-fronted process): a missing header always means owner,
  UNCONDITIONALLY. This is NOT gated on enforce_ownership or loopback: a
  non-enforced Kubernetes deployment's nginx sidecar always sets
  x-forwarded-for on every request it proxies
  (charts/skuld/templates/nginx-configmap.yaml), so neither heuristic could
  ever distinguish the genuine owner from anyone else on that backend, and
  trying to would silently demote every owner connection to viewer —
  exactly the regression this test file guards against. "deployment" mode
  restores this pod's pre-session_participants behavior exactly: participant
  grants are not supported on these backends (invites are refused with 409),
  so room-role gating has nothing to do here anyway.
- "proxy" (rendered only for the process backend): the session proxy stamps
  the header itself, so trust it — a missing header means viewer, except a
  loopback caller with no x-forwarded-for (same-pod tooling a reverse proxy
  could never present as).

_effective_room_role is the ONE function both the middleware and every
role-gated route handler call, so a request is never admitted under one role
and then handled under a different one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skuld.broker_api import _effective_room_role, _enforce_room_role
from skuld.room_role_port import RoomRoleResolutionError


def _request(
    *,
    path: str = "/api/services",
    method: str = "POST",
    room_role_header: str | None = None,
    client_host: str | None = "127.0.0.1",
    x_forwarded_for: str | None = None,
):
    headers = {}
    if room_role_header is not None:
        headers["x-niuu-room-role"] = room_role_header
    if x_forwarded_for is not None:
        headers["x-forwarded-for"] = x_forwarded_for
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        method=method,
        headers=headers,
        client=SimpleNamespace(host=client_host) if client_host is not None else None,
    )


async def _call_next_ok(_request):
    return SimpleNamespace(status_code=200, marker="passed-through")


def _set_room_role_source(monkeypatch, source: str) -> None:
    import skuld.broker_api as broker_api_module

    fake_broker = SimpleNamespace(
        _settings=SimpleNamespace(ws_auth=SimpleNamespace(room_role_source=source))
    )
    monkeypatch.setattr(broker_api_module, "broker", fake_broker)


class TestEffectiveRoomRoleDeploymentMode:
    """room_role_source="deployment" — the default; every non-process backend."""

    async def test_missing_header_is_owner_regardless_of_enforce_ownership_state(self, monkeypatch):
        """Kubernetes today (valhalla): enforce_ownership defaults False, the
        Gateway strips the header, and this must still resolve owner."""
        _set_room_role_source(monkeypatch, "deployment")
        role = await _effective_room_role(_request(room_role_header=None, client_host="10.0.0.5"))
        assert role == "owner"

    async def test_missing_header_is_owner_even_with_x_forwarded_for_present(self, monkeypatch):
        """The nginx sidecar in every Skuld pod always sets x-forwarded-for
        (charts/skuld/templates/nginx-configmap.yaml) — a loopback+no-XFF
        heuristic could never admit the genuine owner on this backend, so
        deployment mode does not use one at all."""
        _set_room_role_source(monkeypatch, "deployment")
        role = await _effective_room_role(
            _request(room_role_header=None, client_host="127.0.0.1", x_forwarded_for="10.0.0.9")
        )
        assert role == "owner"

    async def test_missing_header_is_owner_for_a_non_loopback_caller(self, monkeypatch):
        _set_room_role_source(monkeypatch, "deployment")
        role = await _effective_room_role(
            _request(room_role_header=None, client_host="203.0.113.9")
        )
        assert role == "owner"

    async def test_present_header_still_wins_outright(self, monkeypatch):
        """A valid stamped header is the strongest, most specific signal and
        always wins, even under deployment mode (e.g. Volundr's REST layer
        stamps 'approver' on its own direct-to-broker gate-resolve call)."""
        _set_room_role_source(monkeypatch, "deployment")
        role = await _effective_room_role(
            _request(room_role_header="viewer", client_host="127.0.0.1")
        )
        assert role == "viewer"


class TestEffectiveRoomRoleProxyMode:
    """room_role_source="proxy" — the process backend only."""

    async def test_missing_header_loopback_no_xff_is_owner(self, monkeypatch):
        """Same-pod tooling: containers/skuld/svc, hooks, present-file."""
        _set_room_role_source(monkeypatch, "proxy")
        role = await _effective_room_role(
            _request(room_role_header=None, client_host="127.0.0.1", x_forwarded_for=None)
        )
        assert role == "owner"

    async def test_missing_header_loopback_with_xff_is_viewer(self, monkeypatch):
        """A reverse proxy in front of a loopback-presenting caller always
        adds x-forwarded-for — its presence means this is NOT a genuine
        same-pod caller, so the loopback exception must not apply."""
        _set_room_role_source(monkeypatch, "proxy")
        role = await _effective_room_role(
            _request(room_role_header=None, client_host="127.0.0.1", x_forwarded_for="203.0.113.5")
        )
        assert role == "viewer"

    async def test_missing_header_non_loopback_is_viewer(self, monkeypatch):
        _set_room_role_source(monkeypatch, "proxy")
        role = await _effective_room_role(
            _request(room_role_header=None, client_host="203.0.113.9")
        )
        assert role == "viewer"

    async def test_present_header_wins_outright(self, monkeypatch):
        _set_room_role_source(monkeypatch, "proxy")
        role = await _effective_room_role(
            _request(room_role_header="approver", client_host="203.0.113.9")
        )
        assert role == "approver"


def _remote_request(*, identity_headers: dict[str, str] | None = None, **kwargs):
    request = _request(**kwargs)
    request.headers.update(identity_headers or {})
    return request


def _set_remote_room_role_source(monkeypatch, resolver) -> None:
    """resolver: an object with an async resolve_role(...), or None (unwired)."""
    import skuld.broker_api as broker_api_module

    fake_broker = SimpleNamespace(
        _settings=SimpleNamespace(
            ws_auth=SimpleNamespace(
                room_role_source="remote",
                user_id_header="x-auth-user-id",
                tenant_header="x-auth-tenant",
                roles_header="x-auth-roles",
            )
        ),
        _room_role_resolver=resolver,
        session_id="sess-1",
    )
    monkeypatch.setattr(broker_api_module, "broker", fake_broker)


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


class TestEffectiveRoomRoleRemoteMode:
    """room_role_source="remote" — Kubernetes/OpenShell/VM opted in."""

    @pytest.mark.parametrize("role", ["owner", "approver", "viewer"])
    async def test_resolves_the_role_the_adapter_returns(self, monkeypatch, role):
        resolver = _FakeResolver(role)
        _set_remote_room_role_source(monkeypatch, resolver)
        result = await _effective_room_role(
            _remote_request(identity_headers={"x-auth-user-id": "bob", "x-auth-tenant": "acme"})
        )
        assert result == role
        assert resolver.calls == [
            {"session_id": "sess-1", "user_id": "bob", "tenant_id": "acme", "roles": []}
        ]

    async def test_no_grant_is_none_not_a_default_role(self, monkeypatch):
        resolver = _FakeResolver(None)
        _set_remote_room_role_source(monkeypatch, resolver)
        result = await _effective_room_role(
            _remote_request(identity_headers={"x-auth-user-id": "bob", "x-auth-tenant": "acme"})
        )
        assert result is None

    async def test_no_verified_identity_headers_is_none_without_calling_the_adapter(
        self, monkeypatch
    ):
        resolver = _FakeResolver("owner")
        _set_remote_room_role_source(monkeypatch, resolver)
        result = await _effective_room_role(_remote_request())
        assert result is None
        assert resolver.calls == []

    async def test_adapter_failure_raises_never_falls_back_to_a_role(self, monkeypatch):
        resolver = _FakeResolver(RoomRoleResolutionError("Forge unreachable"))
        _set_remote_room_role_source(monkeypatch, resolver)
        with pytest.raises(RoomRoleResolutionError, match="Forge unreachable"):
            await _effective_room_role(_remote_request(identity_headers={"x-auth-user-id": "bob"}))

    async def test_unwired_resolver_raises_instead_of_defaulting(self, monkeypatch):
        """Defense-in-depth: WsAuthConfig's validator should prevent this, but
        the runtime check must still deny, never silently default a role."""
        _set_remote_room_role_source(monkeypatch, None)
        with pytest.raises(RoomRoleResolutionError, match="no room_role_remote adapter"):
            await _effective_room_role(_remote_request(identity_headers={"x-auth-user-id": "bob"}))

    async def test_present_header_still_wins_outright(self, monkeypatch):
        resolver = _FakeResolver("owner")
        _set_remote_room_role_source(monkeypatch, resolver)
        result = await _effective_room_role(_remote_request(room_role_header="viewer"))
        assert result == "viewer"
        assert resolver.calls == []


class TestEnforceRoomRoleMiddlewareRemoteMode:
    async def test_no_grant_403s(self, monkeypatch):
        _set_remote_room_role_source(monkeypatch, _FakeResolver(None))
        response = await _enforce_room_role(
            _remote_request(identity_headers={"x-auth-user-id": "bob"}), _call_next_ok
        )
        assert response.status_code == 403

    async def test_resolution_failure_503s(self, monkeypatch):
        _set_remote_room_role_source(
            monkeypatch, _FakeResolver(RoomRoleResolutionError("Forge unreachable"))
        )
        response = await _enforce_room_role(
            _remote_request(identity_headers={"x-auth-user-id": "bob"}), _call_next_ok
        )
        assert response.status_code == 503

    async def test_sufficient_role_passes_through(self, monkeypatch):
        _set_remote_room_role_source(monkeypatch, _FakeResolver("owner"))
        response = await _enforce_room_role(
            _remote_request(identity_headers={"x-auth-user-id": "bob"}), _call_next_ok
        )
        assert getattr(response, "marker", None) == "passed-through"


class TestEnforceRoomRoleMiddleware:
    async def test_deployment_mode_missing_header_passes_an_owner_only_route(self, monkeypatch):
        _set_room_role_source(monkeypatch, "deployment")
        response = await _enforce_room_role(
            _request(path="/api/services", room_role_header=None, client_host="10.0.0.5"),
            _call_next_ok,
        )
        assert getattr(response, "marker", None) == "passed-through"

    async def test_proxy_mode_missing_header_non_loopback_403s_an_owner_only_route(
        self, monkeypatch
    ):
        _set_room_role_source(monkeypatch, "proxy")
        response = await _enforce_room_role(
            _request(path="/api/services", room_role_header=None, client_host="203.0.113.9"),
            _call_next_ok,
        )
        assert response.status_code == 403

    async def test_viewer_allowlisted_route_passes_without_any_header_signal(self, monkeypatch):
        _set_room_role_source(monkeypatch, "proxy")
        response = await _enforce_room_role(
            _request(
                path="/api/conversation/history",
                method="GET",
                room_role_header=None,
                client_host="203.0.113.9",
            ),
            _call_next_ok,
        )
        assert getattr(response, "marker", None) == "passed-through"

    async def test_options_preflight_bypasses_the_gate_entirely(self, monkeypatch):
        _set_room_role_source(monkeypatch, "proxy")
        call_next = AsyncMock(return_value=SimpleNamespace(status_code=200, marker="preflight-ok"))
        response = await _enforce_room_role(
            _request(
                path="/api/services", method="OPTIONS", room_role_header=None, client_host=None
            ),
            call_next,
        )
        call_next.assert_awaited_once()
        assert getattr(response, "marker", None) == "preflight-ok"

    async def test_non_api_path_bypasses_the_gate_entirely(self, monkeypatch):
        _set_room_role_source(monkeypatch, "proxy")
        call_next = AsyncMock(return_value=SimpleNamespace(status_code=200, marker="static-ok"))
        response = await _enforce_room_role(
            _request(path="/health", room_role_header=None, client_host=None),
            call_next,
        )
        call_next.assert_awaited_once()
        assert getattr(response, "marker", None) == "static-ok"
