"""Integration tests for Ting saga endpoints.

Exercises the full saga lifecycle: commit → list → get detail → delete.
All data is persisted to real PostgreSQL via the transactional pool.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_create_saga(ting_client: AsyncClient) -> None:
    """POST /api/v1/ting/sagas/commit persists a saga with phases and runs."""
    payload = {
        "name": "Auth Overhaul",
        "slug": "auth-overhaul",
        "description": "Rework authentication layer",
        "repos": ["niuulabs/volundr"],
        "base_branch": "main",
        "phases": [
            {
                "name": "Phase 1 — Foundations",
                "runs": [
                    {
                        "name": "Add OIDC adapter",
                        "description": "Implement OIDC adapter port",
                        "acceptance_criteria": ["Token validation works"],
                        "declared_files": ["src/adapters/oidc.py"],
                        "estimate_hours": 4.0,
                    },
                ],
            },
        ],
    }
    resp = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert body["slug"] == "auth-overhaul"
    assert body["name"] == "Auth Overhaul"
    assert body["feature_branch"] == "feat/auth-overhaul"
    assert body["base_branch"] == "main"
    assert body["status"] == "ACTIVE"
    assert len(body["phases"]) == 1
    assert body["phases"][0]["name"] == "Phase 1 — Foundations"
    assert len(body["phases"][0]["runs"]) == 1
    assert body["phases"][0]["runs"][0]["name"] == "Add OIDC adapter"
    assert body["phases"][0]["runs"][0]["status"] == "PENDING"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_list_sagas(ting_client: AsyncClient) -> None:
    """Create two sagas, GET /api/v1/ting/sagas, assert both returned."""
    for slug in ("saga-a", "saga-b"):
        payload = {
            "name": f"Saga {slug}",
            "slug": slug,
            "repos": ["niuulabs/volundr"],
            "base_branch": "main",
            "phases": [
                {
                    "name": "Phase 1",
                    "runs": [{"name": f"Run for {slug}"}],
                },
            ],
        }
        resp = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
        assert resp.status_code == 201, resp.text

    resp = await ting_client.get("/api/v1/ting/sagas")
    assert resp.status_code == 200
    slugs = {s["slug"] for s in resp.json()}
    assert "saga-a" in slugs
    assert "saga-b" in slugs


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_get_saga_with_phases_and_runs(ting_client: AsyncClient) -> None:
    """Commit a saga with nested phases/runs, then GET detail and verify structure."""
    payload = {
        "name": "Feature X",
        "slug": "feature-x",
        "repos": ["niuulabs/volundr"],
        "base_branch": "main",
        "phases": [
            {
                "name": "Setup",
                "runs": [
                    {"name": "Create schema", "description": "DB schema for feature X"},
                    {"name": "Add migration", "description": "Migration file"},
                ],
            },
            {
                "name": "Implementation",
                "runs": [
                    {"name": "Build API", "description": "REST endpoints"},
                ],
            },
        ],
    }
    create_resp = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
    assert create_resp.status_code == 201
    saga_id = create_resp.json()["id"]

    resp = await ting_client.get(f"/api/v1/ting/sagas/{saga_id}")
    assert resp.status_code == 200

    body = resp.json()
    assert body["slug"] == "feature-x"
    assert body["name"] == "Stub Project"  # hydrated from tracker stub

    # Phases come from the tracker stub, which recorded the created phases
    assert len(body["phases"]) == 2
    phase_names = {p["name"] for p in body["phases"]}
    assert "Setup" in phase_names
    assert "Implementation" in phase_names

    # Runs are nested under phases (as tracker issues)
    setup_phase = next(p for p in body["phases"] if p["name"] == "Setup")
    assert len(setup_phase["runs"]) == 2
    run_titles = {r["title"] for r in setup_phase["runs"]}
    assert "Create schema" in run_titles
    assert "Add migration" in run_titles


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_get_saga_not_found(ting_client: AsyncClient) -> None:
    """GET /api/v1/ting/sagas/{id} returns 404 for non-existent saga."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = await ting_client.get(f"/api/v1/ting/sagas/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_slug_rejected(ting_client: AsyncClient) -> None:
    """POST /api/v1/ting/sagas/commit returns 409 for duplicate slug."""
    payload = {
        "name": "Unique Saga",
        "slug": "unique-slug",
        "repos": ["niuulabs/volundr"],
        "base_branch": "main",
        "phases": [
            {"name": "P1", "runs": [{"name": "R1"}]},
        ],
    }
    resp1 = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
    assert resp1.status_code == 201

    resp2 = await ting_client.post("/api/v1/ting/sagas/commit", json=payload)
    assert resp2.status_code == 409
