"""A host whose people's connections are the Guild's still holds its projects' own."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from starlette.requests import Request

from niuu.adapters.http_integrations import (
    HTTPIntegrationRepository,
    UnsupportedIntegrationQueryError,
)
from niuu.adapters.inbound.auth_context import extract_bearer_token
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.adapters.routed_integrations import RoutedIntegrationRepository
from niuu.domain.models import IntegrationConnection, IntegrationType, project_owner_id
from tests.test_projects.test_team_integrations import ALICE, BOB, DAVE, World
from volundr.adapters.inbound.rest_project_integrations import create_project_integrations_router
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.composition_builders import create_integration_repository
from volundr.config import Settings

NOW = datetime(2026, 11, 22, tzinfo=UTC)
GUILD = "https://guild.example"
PROJECT = str(uuid4())

PERSONAL = IntegrationConnection(
    id=str(uuid4()),
    owner_id="alice",
    integration_type=IntegrationType.ISSUE_TRACKER,
    adapter="a",
    credential_name="mine",
    config={},
    enabled=True,
    created_at=NOW,
    updated_at=NOW,
    slug="linear",
)
TEAM = replace(
    PERSONAL,
    id=str(uuid4()),
    owner_id=project_owner_id(PROJECT),
    project_id=PROJECT,
    credential_name="team-linear",
    connected_by="alice",
)


def http_repository() -> HTTPIntegrationRepository:
    return HTTPIntegrationRepository(
        GUILD,
        api_prefix="/api/v1/integrations",
        auth_adapter="niuu.adapters.outbound.http_auth.RequestBearerTokenAuthAdapter",
    )


def caller(token: str) -> None:
    headers = [(b"authorization", f"Bearer {token}".encode())]
    extract_bearer_token(Request({"type": "http", "query_string": b"", "headers": headers}))


def wire(connection: IntegrationConnection) -> dict:
    return {
        "id": connection.id,
        "owner_id": connection.owner_id,
        "integration_type": str(connection.integration_type),
        "adapter": connection.adapter,
        "credential_name": connection.credential_name,
        "config": connection.config,
        "enabled": connection.enabled,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "slug": connection.slug,
    }


async def routed():
    personal, teams = InMemoryIntegrationRepository(), InMemoryIntegrationRepository()
    repo = RoutedIntegrationRepository(personal, teams)
    await repo.save_connection(PERSONAL)
    await repo.save_connection(TEAM)
    return repo, personal, teams


async def test_each_connection_is_kept_and_found_in_its_own_store():
    repo, personal, teams = await routed()

    assert await personal.list_connections("alice") == [PERSONAL]
    assert await teams.list_connections(TEAM.owner_id) == [TEAM]
    assert await repo.list_connections("alice") == [PERSONAL]
    assert await repo.list_connections(TEAM.owner_id, IntegrationType.MESSAGING) == []
    assert await repo.list_connections(TEAM.owner_id) == [TEAM]
    assert await repo.get_connection(PERSONAL.id) == PERSONAL
    assert await repo.get_connection(TEAM.id) == TEAM
    assert await repo.list_connections_global(slug="linear") == [PERSONAL]


async def test_removing_one_leaves_the_other():
    repo, personal, teams = await routed()

    await repo.delete_connection(TEAM.id)
    assert await repo.get_connection(TEAM.id) is None
    assert await repo.get_connection(PERSONAL.id) == PERSONAL
    await repo.delete_connection(PERSONAL.id)
    assert await personal.list_connections("alice") == []


async def test_a_persons_row_left_in_the_team_store_is_never_answered_from_it():
    personal, teams = InMemoryIntegrationRepository(), InMemoryIntegrationRepository()
    stale = replace(PERSONAL, credential_name="before-the-move")
    await teams.save_connection(stale)
    await personal.save_connection(PERSONAL)
    repo = RoutedIntegrationRepository(personal, teams)

    assert await repo.get_connection(PERSONAL.id) == PERSONAL
    await repo.delete_connection(PERSONAL.id)
    assert await teams.get_connection(PERSONAL.id) == stale


async def test_closing_closes_what_needs_it():
    personal = MagicMock(close=AsyncMock())
    await RoutedIntegrationRepository(personal, InMemoryIntegrationRepository()).close()
    personal.close.assert_awaited_once()
    await RoutedIntegrationRepository(
        InMemoryIntegrationRepository(), InMemoryIntegrationRepository()
    ).close()


async def test_the_callers_own_api_refuses_to_save_a_project_connection():
    with pytest.raises(UnsupportedIntegrationQueryError, match="caller's own connections"):
        await http_repository().save_connection(TEAM)


def test_a_forge_composes_the_store_its_configuration_needs():
    guild = {
        "adapter": "niuu.adapters.http_integrations.HTTPIntegrationRepository",
        "kwargs": {"base_url": GUILD, "api_prefix": "/api/v1/integrations"},
    }
    remote = create_integration_repository(
        Settings(integrations={"repository": guild}), MagicMock(), MagicMock()
    )
    assert isinstance(remote, RoutedIntegrationRepository)

    own = create_integration_repository(Settings(), MagicMock(), MagicMock())
    assert isinstance(own, PostgresIntegrationRepository)

    holding = create_integration_repository(
        Settings(
            integrations={
                "repository": {
                    "adapter": (
                        "volundr.adapters.outbound.memory_integrations."
                        "InMemoryIntegrationRepository"
                    )
                }
            }
        ),
        MagicMock(),
        MagicMock(),
    )
    assert isinstance(holding, InMemoryIntegrationRepository)


@respx.mock
async def test_team_integrations_answer_on_a_host_whose_connections_are_the_guilds():
    """The caller has connections of their own at the Guild; the project's are read here."""
    respx.get(f"{GUILD}/api/v1/integrations").respond(json=[wire(PERSONAL)])
    world = World()
    await world.setup()
    world.service._integrations = RoutedIntegrationRepository(
        http_repository(), InMemoryIntegrationRepository()
    )
    who = {"alice": ALICE, "bob": BOB, "dave": DAVE}

    async def principal(request):
        return who[request.headers["x-test-user"]]

    app = FastAPI()
    app.include_router(create_project_integrations_router(world.service, principal))
    caller("alice-token")
    base = f"/projects/{world.project.id}/integrations"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as api:
        me = {"x-test-user": "alice"}
        assert (await api.get(base, headers=me)).json() == []
        assert {e["slug"] for e in (await api.get(f"{base}/catalog", headers=me)).json()} == {
            "slack",
            "jira",
        }
        await world.slack()
        assert [c["slug"] for c in (await api.get(base, headers=me)).json()] == ["slack"]
        assert (await api.get(base, headers={"x-test-user": "dave"})).status_code == 404
