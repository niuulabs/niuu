"""Integration tests for Volundr stats and models endpoints."""

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


async def test_models_list(volundr_client, auth_headers):
    """GET /api/models returns a non-empty list of available models."""
    headers = auth_headers()
    resp = await volundr_client.get(f"{API}/models", headers=headers)
    assert resp.status_code == 200, resp.text

    models = resp.json()
    assert isinstance(models, list)
    assert len(models) > 0
    # Each model should have at least an id and name
    for m in models:
        assert "id" in m
        assert "name" in m
