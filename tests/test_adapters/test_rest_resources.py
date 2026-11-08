"""Tests for the resource discovery REST endpoint."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from niuu.domain.guild_membership import GuildInstance, GuildMembership
from volundr.adapters.inbound.rest_guild import NOT_JOINED_DETAIL
from volundr.adapters.inbound.rest_resources import (
    create_forge_cluster_resources_router,
    create_resources_router,
)
from volundr.adapters.outbound.static_resource_provider import (
    STANDARD_RESOURCE_TYPES,
    StaticResourceProvider,
)
from volundr.domain.models import ClusterResourceInfo, NodeResourceSummary


@pytest.fixture
def app():
    provider = StaticResourceProvider()
    router = create_resources_router(provider)
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestResourceDiscoveryEndpoint:
    """Tests for GET /api/v1/volundr/resources."""

    @pytest.mark.asyncio
    async def test_returns_resource_types(self, client):
        response = await client.get("/api/v1/volundr/resources")
        assert response.status_code == 200
        data = response.json()
        assert "resource_types" in data
        assert "nodes" in data

        types = data["resource_types"]
        assert len(types) == 2

        names = {t["name"] for t in types}
        assert names == {"cpu", "memory"}

    @pytest.mark.asyncio
    async def test_resource_type_structure(self, client):
        response = await client.get("/api/v1/volundr/resources")
        data = response.json()
        cpu_type = next(t for t in data["resource_types"] if t["name"] == "cpu")
        assert cpu_type["resource_key"] == "cpu"
        assert cpu_type["display_name"] == "CPU"
        assert cpu_type["unit"] == "cores"
        assert cpu_type["category"] == "compute"

    @pytest.mark.asyncio
    async def test_static_provider_returns_no_nodes(self, client):
        response = await client.get("/api/v1/volundr/resources")
        data = response.json()
        assert data["nodes"] == []


NODE_INSTANCE = GuildInstance(id="70b502d8", name="Noatun", slug="noatun")
GUILD_URL = "https://guild.example"


class _OneNodeProvider(StaticResourceProvider):
    async def discover(self) -> ClusterResourceInfo:
        return ClusterResourceInfo(
            resource_types=list(STANDARD_RESOURCE_TYPES),
            nodes=[
                NodeResourceSummary(
                    name="node-a",
                    labels={"kubernetes.io/hostname": "node-a"},
                    allocatable={"cpu": "36", "memory": "70Gi"},
                    allocated={"cpu": "500m", "memory": "1Gi"},
                    available={"cpu": "35500m", "memory": "69Gi"},
                )
            ],
        )


async def _node_client(membership: GuildMembership | None) -> AsyncClient:
    app = FastAPI()
    app.include_router(create_forge_cluster_resources_router(_OneNodeProvider(), membership))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestNodeClusterResources:
    """Tests for the node's own GET /api/v1/forge/cluster/resources."""

    @pytest.mark.asyncio
    async def test_answers_in_the_guild_facade_shape_for_its_own_instance(self):
        async with await _node_client(GuildMembership(GUILD_URL, NODE_INSTANCE)) as client:
            response = await client.get("/api/v1/forge/cluster/resources")
        assert response.status_code == 200
        data = response.json()
        assert data["instances"] == [{"id": "70b502d8", "name": "Noatun", "slug": "noatun"}]
        assert data["resourceTypes"] == data["resource_types"]
        cpu = next(t for t in data["resourceTypes"] if t["name"] == "cpu")
        assert cpu["resourceKey"] == cpu["resource_key"] == "cpu"
        assert cpu["displayName"] == cpu["display_name"] == "CPU"
        assert data["nodes"] == [
            {
                "name": "noatun/node-a",
                "labels": {"kubernetes.io/hostname": "node-a"},
                "allocatable": {"cpu": "36", "memory": "70Gi"},
                "allocated": {"cpu": "500m", "memory": "1Gi"},
                "available": {"cpu": "35500m", "memory": "69Gi"},
                "instance_id": "70b502d8",
                "instance_name": "Noatun",
                "instance_slug": "noatun",
            }
        ]

    @pytest.mark.asyncio
    async def test_accepts_its_own_instance_id(self):
        async with await _node_client(GuildMembership(GUILD_URL, NODE_INSTANCE)) as client:
            response = await client.get(
                "/api/v1/forge/cluster/resources", params={"instance_id": "70b502d8"}
            )
        assert response.status_code == 200
        assert response.json()["instances"][0]["id"] == "70b502d8"

    @pytest.mark.asyncio
    async def test_another_forges_instance_id_is_not_found(self):
        async with await _node_client(GuildMembership(GUILD_URL, NODE_INSTANCE)) as client:
            response = await client.get(
                "/api/v1/forge/cluster/resources", params={"instance_id": "c72f262e"}
            )
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "c72f262e is not this Forge node (Noatun, 70b502d8)" in detail
        assert GUILD_URL in detail

    @pytest.mark.asyncio
    async def test_a_node_outside_any_guild_is_not_found(self):
        async with await _node_client(None) as client:
            response = await client.get("/api/v1/forge/cluster/resources")
        assert response.status_code == 404
        assert response.json()["detail"] == NOT_JOINED_DETAIL

    @pytest.mark.asyncio
    async def test_an_unrecorded_instance_is_unavailable(self):
        async with await _node_client(GuildMembership(GUILD_URL)) as client:
            response = await client.get("/api/v1/forge/cluster/resources")
        assert response.status_code == 503
        assert "instance id there is not recorded" in response.json()["detail"]
