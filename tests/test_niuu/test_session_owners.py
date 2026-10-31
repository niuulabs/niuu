"""A session read goes to the node remembered owning it, not to every node (LEX-1738 follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.inbound.session_owners import SessionOwners
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance

MINI = "https://mini.example"
SPARK = "https://spark.example"
SESSION = "11111111-2222-4333-8444-555555555555"
USER = {"x-auth-user-id": "owner", "x-auth-tenant": "tenant-a"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestSessionOwners:
    def test_remembers_until_the_ttl(self) -> None:
        clock = Clock()
        owners = SessionOwners(60.0, clock=clock)
        owners.learn([("s-1", "mini")])
        clock.now += 59
        assert owners.owner_of("s-1") == "mini"
        clock.now += 1
        assert owners.owner_of("s-1") is None
        assert len(owners) == 0

    def test_learning_prunes_what_expired_and_ignores_blanks(self) -> None:
        clock = Clock()
        owners = SessionOwners(60.0, clock=clock)
        owners.learn([("s-1", "mini"), ("s-2", ""), ("", "mini")])
        clock.now += 61
        owners.learn([("s-3", "spark")])
        assert len(owners) == 1

    def test_forget(self) -> None:
        owners = SessionOwners(60.0)
        owners.learn([("s-1", "mini")])
        owners.forget("s-1")
        assert owners.owner_of("s-1") is None

    def test_a_ttl_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            SessionOwners(0)


def _client(ttl: float | None = 3600.0) -> TestClient:
    instances = [
        _instance("mini", base_url=MINI, is_default=True),
        _instance("spark", base_url=SPARK),
    ]
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(StubInstanceService(instances), session_owner_ttl_seconds=ttl)
    )
    return TestClient(app)


def _listed(client: TestClient) -> None:
    respx.get(f"{MINI}/api/v1/forge/sessions").mock(
        return_value=Response(200, json=[{"id": SESSION, "name": "on the mini"}])
    )
    respx.get(f"{SPARK}/api/v1/forge/sessions").mock(return_value=Response(200, json=[]))
    assert client.get("/api/v1/forge/sessions", headers=USER).status_code == 200


class TestRemembered:
    @respx.mock
    async def test_a_listed_session_is_read_from_its_node_alone(self) -> None:
        client = _client()
        _listed(client)
        mini = respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )
        spark = respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )

        for _ in range(3):
            response = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)
            assert response.status_code == 200
            assert response.json()["instance_id"] == "mini"

        assert mini.call_count == 3
        assert not spark.called

    @respx.mock
    async def test_a_session_that_moved_is_found_by_a_full_search(self) -> None:
        client = _client()
        _listed(client)
        respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )
        spark = respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )

        first = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)
        second = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert first.json()["instance_id"] == second.json()["instance_id"] == "spark"
        # Learned again from the search: the second read asks Spark alone.
        assert spark.call_count == 2

    @respx.mock
    async def test_the_owners_own_failure_is_the_answer(self) -> None:
        client = _client()
        _listed(client)
        respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(return_value=Response(500))
        spark = respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )

        response = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert response.status_code == 502
        assert not spark.called

    @respx.mock
    async def test_without_a_ttl_every_node_is_asked(self) -> None:
        client = _client(ttl=None)
        _listed(client)
        respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )
        spark = respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )

        client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert spark.called
