"""Tests for the resource discovery REST endpoint."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from volundr.adapters.inbound.rest_resources import create_resources_router
from volundr.adapters.outbound.static_resource_provider import StaticResourceProvider


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
