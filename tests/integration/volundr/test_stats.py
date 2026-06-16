"""Integration tests for Forge-owned Volundr runtime endpoints."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"


async def test_stats_empty_db(volundr_client, auth_headers):
    """GET /api/stats on a fresh (rolled-back) DB returns zero counters."""
    headers = auth_headers()
    resp = await volundr_client.get(f"{API}/stats", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["active_sessions"] == 0
    assert body["tokens_today"] == 0
    assert body["cost_today"] == 0.0


async def test_forge_does_not_serve_model_catalog(volundr_client, auth_headers):
    """Model catalog endpoints are owned by Bifrost, not Forge."""
    headers = auth_headers()
    resp = await volundr_client.get(f"{API}/models", headers=headers)
    assert resp.status_code == 404, resp.text
