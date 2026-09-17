"""The user-facing aggregate must preserve the owner's page/error contract."""

import json

import pytest
import respx
from httpx import Response

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance


def owner():
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Synthetic"})
    )
    return client


@respx.mock
def test_aggregate_preserves_exact_full_byte_budget_and_opaque_cursor():
    client = owner()
    payload = {
        "history_protocol": 2,
        "turns": [],
        "total_turns": 0,
        "window_offset": 0,
        "older_cursor": None,
        "padding": "",
    }
    payload["padding"] = "x" * (8192 - len(json.dumps(payload, separators=(",", ":")).encode()))
    route = respx.get("http://beta/api/v1/forge/sessions/s2/conversation").mock(
        return_value=Response(200, json=payload)
    )
    response = client.get(
        "/api/v1/forge/sessions/s2/conversation",
        headers=_headers(),
        params={"history_protocol": 2, "max_bytes": 8192, "cursor": "opaque==-_"},
    )
    assert response.status_code == 200
    assert response.json() == payload
    assert len(response.content) == 8192
    assert route.calls.last.request.url.params["cursor"] == "opaque==-_"


@pytest.mark.parametrize(
    "status,code",
    [(409, "history_cursor_invalid"), (503, "history_busy"), (413, "history_page_too_large")],
)
@respx.mock
def test_aggregate_keeps_typed_recovery_details_not_double_encoded_text(status, code):
    client = owner()
    detail = {"code": code, "recovery": "recent", "history_protocol": 2}
    respx.get("http://beta/api/v1/forge/sessions/s2/conversation").mock(
        return_value=Response(status, json={"detail": detail})
    )
    response = client.get(
        "/api/v1/forge/sessions/s2/conversation", headers=_headers(), params={"history_protocol": 2}
    )
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    if status == 503:
        assert response.headers["Retry-After"] == "1"


@respx.mock
def test_full_item_route_is_owner_scoped_and_proxies_exact_item():
    client = owner()
    payload = {
        "turn": {"id": "row-1", "role": "assistant", "content": "synthetic full text"},
        "projection_revision": "text-items-1:0",
    }
    route = respx.get("http://beta/api/v1/forge/sessions/s2/conversation/turns/row-1").mock(
        return_value=Response(200, json=payload)
    )
    response = client.get("/api/v1/forge/sessions/s2/conversation/turns/row-1", headers=_headers())
    assert response.status_code == 200 and response.json() == payload
    assert route.called


@respx.mock
def test_unknown_history_error_retains_existing_status_failure():
    client = owner()
    respx.get("http://beta/api/v1/forge/sessions/s2/conversation").mock(
        return_value=Response(502, text="upstream unavailable")
    )
    response = client.get("/api/v1/forge/sessions/s2/conversation", headers=_headers())
    assert response.status_code == 502
