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
from niuu.domain.session_owners import SessionOwners
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

    def test_learning_prunes_what_expired_once_per_ttl_and_ignores_blanks(self) -> None:
        clock = Clock()
        owners = SessionOwners(60.0, clock=clock)
        owners.learn([("s-1", "mini"), ("s-2", ""), ("", "mini")])
        assert len(owners) == 1
        clock.now += 30
        owners.learn([("s-2", "mini")])
        clock.now += 31  # s-1 expired; the prune is due
        owners.learn([("s-3", "spark")])
        assert len(owners) == 2  # s-2 (fresh) and s-3

    def test_forget_only_the_owner_that_failed(self) -> None:
        owners = SessionOwners(60.0)
        owners.learn([("s-1", "spark")])  # learned meanwhile by another request
        owners.forget("s-1", "mini")
        assert owners.owner_of("s-1") == "spark"
        owners.forget("s-1", "spark")
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
    async def test_a_remembered_node_that_fails_leaves_it_to_the_full_search(self) -> None:
        client = _client()
        _listed(client)
        respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(return_value=Response(500))
        respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )

        response = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert response.status_code == 200 and response.json()["instance_id"] == "spark"

    @respx.mock
    async def test_a_failing_node_still_makes_the_search_partial(self) -> None:
        client = _client()
        _listed(client)
        respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(return_value=Response(500))
        respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )

        response = client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        # Mini did not answer, so "not found" cannot be claimed.
        assert response.status_code == 502

    @respx.mock
    async def test_the_node_that_said_not_here_is_not_asked_again(self) -> None:
        client = _client()
        _listed(client)
        mini = respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )
        respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )

        client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert mini.call_count == 1

    @respx.mock
    async def test_a_session_listed_by_two_nodes_is_not_pinned(self) -> None:
        client = _client()
        respx.get(f"{MINI}/api/v1/forge/sessions").mock(
            return_value=Response(200, json=[{"id": SESSION}])
        )
        respx.get(f"{SPARK}/api/v1/forge/sessions").mock(
            return_value=Response(200, json=[{"id": SESSION}])
        )
        client.get("/api/v1/forge/sessions", headers=USER)
        mini = respx.get(f"{MINI}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )
        spark = respx.get(f"{SPARK}/api/v1/forge/sessions/{SESSION}").mock(
            return_value=Response(200, json={"id": SESSION})
        )

        client.get(f"/api/v1/forge/sessions/{SESSION}", headers=USER)

        assert mini.called and spark.called

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


class TestParentGuildId:
    @respx.mock
    async def test_a_stored_projects_id_is_reported_with_its_guild_id(self) -> None:
        client = _client()
        respx.get(f"{MINI}/api/v1/forge/instance").mock(
            return_value=Response(200, json={"instance_id": "mini-m6"})
        )
        spark_identity = respx.get(f"{SPARK}/api/v1/forge/instance").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )
        respx.get(f"{MINI}/api/v1/forge/sessions").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": "w-1",
                        "coordination": {"parent": {"instance_id": "mini-m6", "session_id": "p"}},
                    },
                    {
                        "id": "w-2",
                        "coordination": {"parent": {"instance_id": "spark", "session_id": "p"}},
                    },
                    {
                        "id": "w-3",
                        "coordination": {"parent": {"instance_id": "elsewhere", "session_id": "p"}},
                    },
                ],
            )
        )
        respx.get(f"{SPARK}/api/v1/forge/sessions").mock(return_value=Response(200, json=[]))

        for _ in range(2):
            items = client.get("/api/v1/forge/sessions", headers=USER).json()

        parents = {item["id"]: item["coordination"]["parent"] for item in items}
        # The stored id stays; the Guild id is added beside it.
        assert parents["w-1"] == {
            "instance_id": "mini-m6",
            "session_id": "p",
            "guild_instance_id": "mini",
        }
        assert parents["w-2"]["guild_instance_id"] == "spark"  # already a Guild id
        assert "guild_instance_id" not in parents["w-3"]  # unknown: nothing invented
        assert spark_identity.call_count == 1  # an older Forge is asked once per TTL
