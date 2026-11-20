"""The Guild facade for LEX-1936: native-control cards and the explicit message refusal."""

from __future__ import annotations

import respx
from httpx import Response

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

SESSIONS = "http://beta/api/v1/forge/sessions"
CARD = {
    "request_id": "tty-1-c3b257b9",
    "kind": "permission",
    "opened_at": "2026-11-05T20:36:56Z",
    "age_seconds": 2340.0,
    "menu_state": "gone",
}


def _owned_by_beta():
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get(f"{SESSIONS}/s2").mock(return_value=Response(200, json={"id": "s2", "name": "S2"}))
    return client


@respx.mock
def test_native_controls_are_read_on_the_owning_host() -> None:
    client = _owned_by_beta()
    listed = respx.get(f"{SESSIONS}/s2/native-controls").mock(
        return_value=Response(200, json={"controls": [CARD]})
    )

    response = client.get("/api/v1/forge/sessions/s2/native-controls", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"controls": [CARD]}
    assert listed.called


@respx.mock
def test_a_dismissal_goes_to_the_owning_host() -> None:
    client = _owned_by_beta()
    dismissed = respx.post(f"{SESSIONS}/s2/native-controls/tty-1-c3b257b9/dismiss").mock(
        return_value=Response(200, json={"dismissed": True, "request_id": "tty-1-c3b257b9"})
    )

    response = client.post(
        "/api/v1/forge/sessions/s2/native-controls/tty-1-c3b257b9/dismiss", headers=_headers()
    )

    assert response.status_code == 200
    assert response.json() == {"dismissed": True, "request_id": "tty-1-c3b257b9"}
    assert dismissed.called


@respx.mock
def test_the_owning_hosts_refusal_is_returned() -> None:
    client = _owned_by_beta()
    respx.post(f"{SESSIONS}/s2/native-controls/tty-9/dismiss").mock(
        return_value=Response(403, json={"detail": "Only the session owner or an admin may"})
    )

    response = client.post(
        "/api/v1/forge/sessions/s2/native-controls/tty-9/dismiss", headers=_headers()
    )

    assert response.status_code == 403
    assert "session owner" in response.json()["detail"]


@respx.mock
def test_an_old_host_without_the_routes_is_named_as_such() -> None:
    client = _owned_by_beta()
    respx.get(f"{SESSIONS}/s2/native-controls").mock(
        return_value=Response(404, json={"detail": "Not Found"})
    )

    response = client.get("/api/v1/forge/sessions/s2/native-controls", headers=_headers())

    assert response.status_code == 501
    assert "Update this session's Forge host" in response.json()["detail"]


@respx.mock
def test_a_message_refused_for_a_pending_native_control_keeps_its_code() -> None:
    client = _owned_by_beta()
    refusal = {
        "detail": {
            "code": "native_control_pending",
            "message": "Claude is waiting on a native control; answer it first",
            "control_request_id": "tty-1-c3b257b9",
            "menu_state": "live",
        }
    }
    respx.post(f"{SESSIONS}/s2/messages").mock(return_value=Response(409, json=refusal))

    response = client.post(
        "/api/v1/forge/sessions/s2/messages",
        headers=_headers(),
        json={"content": "steer", "request_id": "m-1"},
    )

    assert response.status_code == 409
    assert response.json() == refusal


@respx.mock
def test_any_other_message_refusal_stays_a_plain_error() -> None:
    client = _owned_by_beta()
    respx.post(f"{SESSIONS}/s2/messages").mock(
        return_value=Response(409, json={"detail": "Session is not running"})
    )

    response = client.post(
        "/api/v1/forge/sessions/s2/messages",
        headers=_headers(),
        json={"content": "steer", "request_id": "m-2"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Session is not running"}


@respx.mock
def test_a_reconcile_goes_to_the_owning_host() -> None:
    """LEX-1979: the Guild facade forwards an owner's question reconcile."""
    client = _owned_by_beta()
    reconciled = respx.post(f"{SESSIONS}/s2/native-controls/reconcile").mock(
        return_value=Response(200, json={"checked": 0, "closed": [], "kept": []})
    )
    response = client.post(
        "/api/v1/forge/sessions/s2/native-controls/reconcile", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json() == {"checked": 0, "closed": [], "kept": []}
    assert reconciled.called
