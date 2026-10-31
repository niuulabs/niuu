"""GET /api/v1/forge/guild through the Guild facade: it describes the Guild itself."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.domain.guild_membership import GuildInstance, GuildMembership
from niuu.domain.models import RegisteredInstance
from tests.test_niuu.test_rest_volundr import StubInstanceService, _headers, _instance

GUILD_URL = "https://yggdrasil.niuu.world"


def _client(instances: list[RegisteredInstance], membership: GuildMembership | None) -> TestClient:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(
            StubInstanceService(instances),  # type: ignore[arg-type]
            embedded_forge_app=FastAPI(),
            guild_membership=membership,
        )
    )
    return TestClient(app)


def _guild(client: TestClient):
    return client.get("/api/v1/forge/guild", headers=_headers())


def test_describes_the_guild_and_its_default_node() -> None:
    client = _client(
        [
            _instance("noatun", base_url="https://volundr.noatun.example"),
            _instance("valhalla", base_url="https://volundr.valhalla.example", is_default=True),
        ],
        GuildMembership(guild_url=GUILD_URL),
    )

    response = _guild(client)

    assert response.status_code == 200
    assert response.json() == {
        "guild_url": GUILD_URL,
        "instance_id": "valhalla",
        "instance_name": "Instance valhalla",
        "instance_slug": "valhalla",
    }


def test_prefers_the_embedded_node_over_the_registry_default() -> None:
    client = _client(
        [
            _instance("valhalla", base_url="https://volundr.valhalla.example", is_default=True),
            _instance("local", base_url="embedded://local-forge", config={"transport": "embedded"}),
        ],
        GuildMembership(guild_url=GUILD_URL),
    )

    assert _guild(client).json()["instance_id"] == "local"


def test_instance_fields_are_null_without_an_embedded_or_default_node() -> None:
    client = _client(
        [_instance("noatun", base_url="https://volundr.noatun.example")],
        GuildMembership(guild_url=GUILD_URL),
    )

    assert _guild(client).json() == {
        "guild_url": GUILD_URL,
        "instance_id": None,
        "instance_name": None,
        "instance_slug": None,
    }


def test_a_joined_host_names_its_own_instance_in_the_remote_guild() -> None:
    joined = GuildInstance(id="ae40", name="Mac", slug="mac")
    client = _client(
        [_instance("local", base_url="embedded://local-forge", config={"transport": "embedded"})],
        GuildMembership(guild_url=GUILD_URL, instance=joined),
    )

    assert _guild(client).json() == {
        "guild_url": GUILD_URL,
        "instance_id": "ae40",
        "instance_name": "Mac",
        "instance_slug": "mac",
    }


def test_answers_404_when_this_host_is_not_configured_as_part_of_a_guild() -> None:
    client = _client([_instance("valhalla", base_url="https://v.example", is_default=True)], None)

    assert _guild(client).status_code == 404
