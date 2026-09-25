"""skuld.broker_api._enforce_room_role: the HTTP defense-in-depth room-role
gate, independent of the session proxy's own enforcement.

A missing x-niuu-room-role header must NOT always mean "viewer" — that broke
every in-pod/K8s-owner caller with no way to carry the header at all (Claude
hooks, present-file, containers/skuld/svc, Ravn room join, Ting workflow
events, Volundr's own GET /api/communication/routes). It must mirror
websocket_lifecycle._resolve_room_role: missing header -> owner when
ws_auth.enforce_ownership is on, OR when the caller is loopback with no
x-forwarded-for (same-pod tooling a reverse proxy could never present as).
Only when neither applies does a missing header mean "viewer".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from skuld.broker_api import _enforce_room_role


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


def _set_enforce_ownership(monkeypatch, value: bool) -> None:
    import skuld.broker_api as broker_api_module

    fake_broker = SimpleNamespace(
        _settings=SimpleNamespace(ws_auth=SimpleNamespace(enforce_ownership=value))
    )
    monkeypatch.setattr(broker_api_module, "broker", fake_broker)


async def test_missing_header_with_enforce_ownership_on_is_owner(monkeypatch):
    """The Kubernetes-hardened-deployment case: the owner, already verified
    by Envoy's ext_authz before this pod ever saw the request, must not be
    403'd just because the header carries no claim about it."""
    _set_enforce_ownership(monkeypatch, True)
    # A route with no explicit allowlist entry defaults to owner-only.
    response = await _enforce_room_role(
        _request(path="/api/services", room_role_header=None, client_host="10.0.0.5"),
        _call_next_ok,
    )
    assert getattr(response, "marker", None) == "passed-through"


async def test_missing_header_with_enforce_ownership_off_and_loopback_is_owner(monkeypatch):
    """The in-pod tooling case (containers/skuld/svc, hooks, present-file):
    loopback with no x-forwarded-for cannot be presented by an external
    caller behind any reverse proxy, so it is treated as owner."""
    _set_enforce_ownership(monkeypatch, False)
    response = await _enforce_room_role(
        _request(
            path="/api/services",
            room_role_header=None,
            client_host="127.0.0.1",
            x_forwarded_for=None,
        ),
        _call_next_ok,
    )
    assert getattr(response, "marker", None) == "passed-through"


async def test_missing_header_loopback_but_with_x_forwarded_for_is_viewer(monkeypatch):
    """A reverse proxy in front of a loopback-presenting caller always adds
    x-forwarded-for — its presence means this is NOT a genuine same-pod
    caller, so the loopback exception must not apply."""
    _set_enforce_ownership(monkeypatch, False)
    response = await _enforce_room_role(
        _request(
            path="/api/services",
            room_role_header=None,
            client_host="127.0.0.1",
            x_forwarded_for="203.0.113.5",
        ),
        _call_next_ok,
    )
    assert response.status_code == 403


async def test_missing_header_non_loopback_no_enforcement_is_viewer(monkeypatch):
    """The ordinary mini-mode external caller case: no header, not loopback,
    ownership not enforced here -> least privilege."""
    _set_enforce_ownership(monkeypatch, False)
    response = await _enforce_room_role(
        _request(path="/api/services", room_role_header=None, client_host="203.0.113.9"),
        _call_next_ok,
    )
    assert response.status_code == 403


async def test_present_header_is_trusted_over_loopback_defaults(monkeypatch):
    """A valid stamped header always wins outright, regardless of loopback
    or enforce_ownership — it is the strongest, most specific signal."""
    _set_enforce_ownership(monkeypatch, True)
    response = await _enforce_room_role(
        _request(path="/api/services", room_role_header="viewer", client_host="127.0.0.1"),
        _call_next_ok,
    )
    assert response.status_code == 403


async def test_viewer_allowlisted_route_passes_without_any_header_signal(monkeypatch):
    """A route in the viewer allowlist needs no elevated signal at all."""
    _set_enforce_ownership(monkeypatch, False)
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


async def test_options_preflight_bypasses_the_gate_entirely(monkeypatch):
    _set_enforce_ownership(monkeypatch, False)
    call_next = AsyncMock(return_value=SimpleNamespace(status_code=200, marker="preflight-ok"))
    response = await _enforce_room_role(
        _request(path="/api/services", method="OPTIONS", room_role_header=None, client_host=None),
        call_next,
    )
    call_next.assert_awaited_once()
    assert getattr(response, "marker", None) == "preflight-ok"


async def test_non_api_path_bypasses_the_gate_entirely(monkeypatch):
    _set_enforce_ownership(monkeypatch, False)
    call_next = AsyncMock(return_value=SimpleNamespace(status_code=200, marker="static-ok"))
    response = await _enforce_room_role(
        _request(path="/health", room_role_header=None, client_host=None),
        call_next,
    )
    call_next.assert_awaited_once()
    assert getattr(response, "marker", None) == "static-ok"
