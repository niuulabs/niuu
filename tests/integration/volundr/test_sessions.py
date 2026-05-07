"""Integration tests for Volundr session endpoints.

Each test creates its own data, uses ``auth_headers`` for identity,
and relies on transaction rollback for cleanup.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"


async def _create_session(client, headers, name="test-session"):
    """Helper: POST a minimal session creation request."""
    payload = {
        "name": name,
        "model": "claude-sonnet-4-6",
        "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
    }
    return await client.post(f"{API}/sessions", json=payload, headers=headers)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_create_session(volundr_client, auth_headers):
    """POST /api/sessions creates a session and returns 201."""
    headers = auth_headers("user-create", "create@test.com", "default", ["volundr:admin"])
    resp = await _create_session(volundr_client, headers, name="integ-create")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "integ-create"
    assert body["status"] in ("created", "starting", "provisioning", "running", "failed")
    assert "id" in body


async def test_list_sessions(volundr_client, auth_headers):
    """Create 3 sessions, GET /sessions, assert all returned."""
    headers = auth_headers("user-list", "list@test.com", "default", ["volundr:admin"])

    names = ["integ-list-a", "integ-list-b", "integ-list-c"]
    for name in names:
        resp = await _create_session(volundr_client, headers, name=name)
        assert resp.status_code == 201, resp.text

    resp = await volundr_client.get(f"{API}/sessions", headers=headers)
    assert resp.status_code == 200, resp.text

    returned_names = {s["name"] for s in resp.json()}
    for name in names:
        assert name in returned_names


async def test_get_session(volundr_client, auth_headers):
    """Create then GET by ID, assert fields match."""
    headers = auth_headers("user-get", "get@test.com", "default", ["volundr:admin"])
    create_resp = await _create_session(volundr_client, headers, name="integ-get")
    assert create_resp.status_code == 201, create_resp.text

    session_id = create_resp.json()["id"]
    get_resp = await volundr_client.get(f"{API}/sessions/{session_id}", headers=headers)

    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["id"] == session_id
    assert body["name"] == "integ-get"


async def test_delete_session(volundr_client, auth_headers):
    """Create, DELETE, then GET should return 404."""
    headers = auth_headers("user-del", "del@test.com", "default", ["volundr:admin"])
    create_resp = await _create_session(volundr_client, headers, name="integ-del")
    assert create_resp.status_code == 201, create_resp.text

    session_id = create_resp.json()["id"]

    del_resp = await volundr_client.delete(f"{API}/sessions/{session_id}", headers=headers)
    assert del_resp.status_code == 204, del_resp.text

    get_resp = await volundr_client.get(f"{API}/sessions/{session_id}", headers=headers)
    assert get_resp.status_code == 404


async def test_create_session_invalid_name(volundr_client, auth_headers):
    """POST with an invalid name (uppercase) should return 422."""
    headers = auth_headers("user-bad", "bad@test.com", "default", ["volundr:admin"])
    payload = {
        "name": "INVALID_NAME",
        "model": "claude-sonnet-4-6",
        "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
    }
    resp = await volundr_client.post(f"{API}/sessions", json=payload, headers=headers)
    assert resp.status_code == 422


async def test_session_owner_filtering(volundr_client, auth_headers):
    """Session created by user A should not appear in user B's list."""
    headers_a = auth_headers("owner-a", "a@test.com", "default", ["volundr:developer"])
    headers_b = auth_headers("owner-b", "b@test.com", "default", ["volundr:developer"])

    create_resp = await _create_session(volundr_client, headers_a, name="integ-owner")
    assert create_resp.status_code == 201, create_resp.text

    # User B lists sessions — should not see user A's session
    list_resp = await volundr_client.get(f"{API}/sessions", headers=headers_b)
    assert list_resp.status_code == 200, list_resp.text

    returned_ids = {s["id"] for s in list_resp.json()}
    assert create_resp.json()["id"] not in returned_ids
