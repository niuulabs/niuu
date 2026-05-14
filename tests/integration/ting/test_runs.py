"""Integration tests for Ting run endpoints.

Exercises the runs summary endpoint that queries PostgreSQL directly,
verifying that committed runs are counted correctly by status.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_run_summary_empty(ting_client: AsyncClient) -> None:
    """GET /api/v1/ting/runs/summary returns zero counts when no runs exist."""
    resp = await ting_client.get("/api/v1/ting/runs/summary")
    assert resp.status_code == 200

    body = resp.json()
    assert body["PENDING"] == 0
    assert body["RUNNING"] == 0
    assert body["MERGED"] == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_run_summary_after_commit(ting_client: AsyncClient) -> None:
    """Commit a saga with runs, then GET /summary and verify PENDING count."""
    payload = {
        "name": "Run Count Test",
        "slug": "run-count-test",
        "repos": ["niuulabs/volundr"],
        "base_branch": "main",
        "phases": [
            {
                "name": "Phase 1",
                "runs": [
                    {"name": "Run A"},
                    {"name": "Run B"},
                    {"name": "Run C"},
                ],
            },
        ],
    }
    resp = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
    assert resp.status_code == 201

    summary_resp = await ting_client.get("/api/v1/ting/runs/summary")
    assert summary_resp.status_code == 200

    body = summary_resp.json()
    assert body["PENDING"] == 3
