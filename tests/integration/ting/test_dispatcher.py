"""Integration tests for Ting dispatcher endpoints.

Exercises GET and PATCH on dispatcher state, verifying SQL round-trips
against real PostgreSQL.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_get_dispatcher_state(ting_client: AsyncClient) -> None:
    """GET /api/v1/ting/dispatcher creates a default state and returns it."""
    resp = await ting_client.get("/api/v1/ting/dispatcher")
    assert resp.status_code == 200

    body = resp.json()
    assert body["running"] is True
    assert body["threshold"] == 0.75
    assert body["max_concurrent_runs"] == 3
    assert "id" in body
    assert "updated_at" in body


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_patch_dispatcher_state(ting_client: AsyncClient) -> None:
    """PATCH /api/v1/ting/dispatcher updates fields and returns new state."""
    # Ensure a default row exists first
    await ting_client.get("/api/v1/ting/dispatcher")

    resp = await ting_client.patch(
        "/api/v1/ting/dispatcher",
        json={
            "running": False,
            "threshold": 0.50,
            "max_concurrent_runs": 5,
        },
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body["running"] is False
    assert body["threshold"] == 0.50
    assert body["max_concurrent_runs"] == 5

    # Verify persistence via a fresh GET
    verify_resp = await ting_client.get("/api/v1/ting/dispatcher")
    verify_body = verify_resp.json()
    assert verify_body["running"] is False
    assert verify_body["threshold"] == 0.50
    assert verify_body["max_concurrent_runs"] == 5
