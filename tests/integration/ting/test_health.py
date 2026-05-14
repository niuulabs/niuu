"""Integration test for Ting health endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_health_endpoint(ting_client: AsyncClient) -> None:
    """GET /health returns 200 with status ok."""
    resp = await ting_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
