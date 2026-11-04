"""REST contract for artifact publications, bursts and host identity (F1, F1n, F7)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.support.artifacts import T0, candidate, hook_row, service
from volundr.adapters.inbound.rest_artifacts import (
    InstanceIdentity,
    create_artifacts_router,
    create_instance_router,
)
from volundr.adapters.outbound.memory_artifacts import InMemoryArtifactPublicationRepository
from volundr.domain.artifacts import Origin

PREFIX = "/api/v1/forge"


class RecordingRepo(InMemoryArtifactPublicationRepository):
    def __init__(self) -> None:
        super().__init__()
        self.scopes: list = []

    async def list_after(self, scope, *, after, limit):
        self.scopes.append(scope)
        return await super().list_after(scope, after=after, limit=limit)

    async def list_bursts_after(self, scope, *, after, limit):
        self.scopes.append(scope)
        return await super().list_bursts_after(scope, after=after, limit=limit)

    async def burst_publications(self, burst_id, scope=None):
        self.scopes.append(scope)
        return await super().burst_publications(burst_id, scope)


def headers(user: str = "owner-a", *, admin: bool = False) -> dict[str, str]:
    return {
        "x-auth-user-id": user,
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "volundr:admin" if admin else "volundr:developer",
    }


@pytest.fixture
def env():
    repo = RecordingRepo()
    svc = service(repo, "thor-exp")
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(
        create_artifacts_router(svc, prefix=PREFIX, default_page_size=2, max_page_size=5)
    )
    app.include_router(
        create_instance_router(
            InstanceIdentity(host="thor-exp", tailnet_name="thor.ts.net", instance_id="thor-exp")
        )
    )
    return TestClient(app), svc, repo


async def _seed(svc, count: int = 3):
    items = [
        (candidate("K", "s1", seq, T0 + timedelta(seconds=seq)), Origin.LIVE)
        for seq in range(1, count + 1)
    ]
    return await svc.record(items)


async def test_publications_page_with_cursor_and_owner_scope(env):
    client, svc, repo = env
    await _seed(svc)

    first = client.get(f"{PREFIX}/artifact-publications", headers=headers()).json()
    assert first["host"] == "thor-exp"
    assert [item["session_seq"] for item in first["items"]] == [1, 2]
    assert first["has_more"] is True
    assert first["items"][0]["burst_id"] is not None
    assert first["items"][0]["occurrence"][1] == "log"

    rest = client.get(
        f"{PREFIX}/artifact-publications",
        params={"after": first["next_after"]},
        headers=headers(),
    ).json()
    assert [item["session_seq"] for item in rest["items"]] == [3]
    assert rest["has_more"] is False

    empty = client.get(
        f"{PREFIX}/artifact-publications", params={"after": 99}, headers=headers()
    ).json()
    assert empty == {"host": "thor-exp", "items": [], "next_after": 99, "has_more": False}

    scope = repo.scopes[0]
    assert (scope.user_id, scope.is_admin) == ("owner-a", False)


def test_publications_limit_is_bounded(env):
    client, _, _ = env
    response = client.get(f"{PREFIX}/artifact-publications", params={"limit": 6}, headers=headers())
    assert response.status_code == 422


async def test_bursts_feed_follows_changes(env):
    client, svc, repo = env
    await _seed(svc, 1)
    page = client.get(f"{PREFIX}/artifact-bursts", headers=headers(admin=True)).json()
    [burst] = page["items"]
    assert burst["rev"] == page["next_after"]
    assert burst["origin"] == "live" and burst["publications"] == 1
    assert repo.scopes[-1].is_admin is True

    await svc.record([(candidate("K", "s1", 9, T0 + timedelta(minutes=5)), Origin.LIVE)])
    later = client.get(
        f"{PREFIX}/artifact-bursts", params={"after": page["next_after"]}, headers=headers()
    ).json()
    assert [b["publications"] for b in later["items"]] == [2]
    assert later["has_more"] is False


async def test_get_burst_and_missing_burst(env):
    client, svc, _ = env
    [pub, *_] = await _seed(svc, 2)
    body = client.get(f"{PREFIX}/artifact-bursts/{pub.burst_id}", headers=headers()).json()
    assert body["burst_id"] == str(pub.burst_id)
    assert [item["session_seq"] for item in body["items"]] == [1, 2]

    missing = client.get(f"{PREFIX}/artifact-bursts/{uuid4()}", headers=headers())
    assert missing.status_code == 404


async def test_get_burst_without_visible_rows_is_404(env):
    client, svc, repo = env
    [pub] = await _seed(svc, 1)

    async def hidden(burst_id, scope=None):
        return []

    repo.burst_publications = hidden
    response = client.get(f"{PREFIX}/artifact-bursts/{pub.burst_id}", headers=headers())
    assert response.status_code == 404


async def test_backfill_needs_admin_and_reports(env):
    client, svc, repo = env
    url = "https://claude.ai/artifact/Bf1"
    repo.add_log_rows(
        [
            hook_row(
                str(uuid4()),
                1,
                T0,
                {
                    "tool_name": "Artifact",
                    "tool_use_id": "toolu_1",
                    "tool_input": {},
                    "tool_response": {"url": url, "version": "1-a"},
                },
            )
        ]
    )
    denied = client.post(f"{PREFIX}/artifact-publications/backfill", json={}, headers=headers())
    assert denied.status_code == 403

    response = client.post(
        f"{PREFIX}/artifact-publications/backfill",
        json={"since": "2026-10-01T00:00:00Z"},
        headers=headers(admin=True),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recorded"] == 1 and body["scanned_rows"] == 1
    assert body["origin"] == "backfill" and body["last_seq"] == 1
    assert body["last_session_id"] is not None


def test_backfill_with_nothing_to_scan(env):
    client, _, _ = env
    body = client.post(
        f"{PREFIX}/artifact-publications/backfill", json={}, headers=headers(admin=True)
    ).json()
    assert body["recorded"] == 0 and body["last_session_id"] is None


def test_instance_identity(env):
    client, _, _ = env
    assert client.get(f"{PREFIX}/instance").json() == {
        "host": "thor-exp",
        "tailnet_name": "thor.ts.net",
        "instance_id": "thor-exp",
        "service": "forge-api",
    }
