"""Integration tests for Volundr authentication and header propagation."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"


async def test_health_no_auth(volundr_client):
    """GET /health returns 200 without any auth headers."""
    resp = await volundr_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


async def test_auth_headers_propagate_principal(volundr_client, auth_headers):
    """Session owner_id should match the x-auth-user-id header."""
    user_id = "principal-check"
    headers = auth_headers(user_id, "check@test.com", "default", ["volundr:admin"])

    payload = {
        "name": "integ-auth",
        "model": "claude-sonnet-4-6",
        "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
    }
    resp = await volundr_client.post(f"{API}/sessions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["owner_id"] == user_id
