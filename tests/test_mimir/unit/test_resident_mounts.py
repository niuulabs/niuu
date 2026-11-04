"""Resident memory: stores residents serve, mounted here by name only."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.adapters.valkyrie_memory import ValkyrieMemoryDirectory
from mimir.ports.resident_memory import ResidentMemoryDirectoryPort, ServedResidentMemory
from mimir.router import MimirRouter
from ravn.adapters.channels.gateway import RavnGateway
from ravn.adapters.channels.gateway_http import HttpGateway
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.config import HttpChannelConfig

PLATFORM = {"x-auth-user-id": "jozef", "x-auth-tenant": "default", "x-auth-roles": "volundr:admin"}
OTHER_ORG = {"x-auth-user-id": "x", "x-auth-tenant": "acme", "x-auth-roles": "volundr:admin"}
REGIN = ServedResidentMemory(
    valkyrie_id="regin", name="resident-regin", role="local", url="https://regin.test"
)


class Directory(ResidentMemoryDirectoryPort):
    def __init__(self, served):
        self.calls = 0
        self._served = served

    async def served(self):
        self.calls += 1
        if isinstance(self._served, Exception):
            raise self._served
        return self._served


def _resident_gateway(store) -> httpx.AsyncBaseTransport:
    verifier = MagicMock()
    verifier.verify = AsyncMock(
        side_effect=lambda token: {"workload_service": "mimir"} if token == "platform" else {}
    )
    gateway = HttpGateway(
        HttpChannelConfig(
            enabled=True,
            public_url="https://regin.test",
            mimir_served_mount="local",
            mimir_required_claims={"workload_service": "mimir"},
        ),
        MagicMock(spec=RavnGateway),
        mimir=store,
        mimir_verifier=verifier,
    )
    return httpx.ASGITransport(app=gateway.app)


async def _client(tmp_path, directory, *, resident_port=None):
    """A platform Mímir; *resident_port* stands in for Regin's served store."""
    shared = MarkdownMimirAdapter(root=tmp_path / "shared")
    await shared.upsert_page("platform/home.md", "# Home\n\nThe platform's own page.\n")
    router = MimirRouter(
        shared,
        name="shared",
        auth=EnvoyHeaderAuthenticationAdapter(),
        resident_memory=directory,
        resident_mount_auth={"type": "bearer", "token": "platform"},
    )
    if resident_port is not None:
        router._resident_ports[(REGIN.name, REGIN.url)] = resident_port
    app = FastAPI()
    app.include_router(router.router)
    return TestClient(app)


def _through_gateway(store) -> HttpMimirAdapter:
    port = HttpMimirAdapter(base_url=REGIN.url, auth={"type": "bearer", "token": "platform"})
    port._client = httpx.AsyncClient(transport=_resident_gateway(store), base_url=REGIN.url)
    return port


async def _regin_store(tmp_path):
    store = MarkdownMimirAdapter(root=tmp_path / "regin")
    await store.upsert_page("learnings/release.md", "# Release\n\nShip on Thursdays.\n")
    return store


async def test_a_resident_store_opens_by_name_through_its_gateway(tmp_path):
    client = await _client(
        tmp_path, Directory([REGIN]), resident_port=_through_gateway(await _regin_store(tmp_path))
    )

    page = client.get(
        "/page",
        headers=PLATFORM,
        params={"path": "learnings/release.md", "mount": "resident-regin"},
    )
    mounts = {m["name"]: m for m in client.get("/mounts", headers=PLATFORM).json()}

    assert page.status_code == 200
    assert "Ship on Thursdays" in page.json()["content"]
    assert mounts["resident-regin"]["status"] == "healthy"
    assert mounts["resident-regin"]["pages"] == 1


async def test_merged_reads_never_ask_residents_or_the_directory(tmp_path):
    directory = Directory([REGIN])
    client = await _client(
        tmp_path, directory, resident_port=_through_gateway(await _regin_store(tmp_path))
    )

    pages = client.get("/pages", headers=PLATFORM).json()
    own = client.get("/pages", headers=PLATFORM, params={"mount": "shared"})

    assert [page["path"] for page in pages] == ["platform/home.md"]
    assert own.status_code == 200
    assert directory.calls == 0


async def test_a_resident_that_is_down_is_listed_down_and_breaks_nothing_else(tmp_path):
    down = HttpMimirAdapter(base_url=REGIN.url, auth={"type": "bearer", "token": "platform"})
    down._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("connection refused"))
        ),
        base_url=REGIN.url,
    )
    client = await _client(tmp_path, Directory([REGIN]), resident_port=down)

    mounts = {m["name"]: m for m in client.get("/mounts", headers=PLATFORM).json()}
    pages = client.get("/pages", headers=PLATFORM)

    assert mounts["resident-regin"]["status"] == "down"
    assert "regin did not answer" in mounts["resident-regin"]["desc"]
    assert mounts["shared"]["status"] == "healthy"
    assert pages.status_code == 200
    with pytest.raises(httpx.ConnectError):
        client.get("/page", headers=PLATFORM, params={"path": "x.md", "mount": "resident-regin"})


async def test_the_directory_failing_never_touches_the_hosts_own_reads(tmp_path):
    client = await _client(tmp_path, Directory(httpx.ConnectError("directory down")))

    assert client.get("/pages", headers=PLATFORM).status_code == 200
    assert client.get("/pages", headers=PLATFORM, params={"mount": "shared"}).status_code == 200
    with pytest.raises(httpx.ConnectError):
        client.get("/mounts", headers=PLATFORM)


async def test_other_orgs_never_see_resident_memory(tmp_path):
    directory = Directory([REGIN])
    client = await _client(tmp_path, directory)

    names = [m["name"] for m in client.get("/mounts", headers=OTHER_ORG).json()]
    page = client.get(
        "/page", headers=OTHER_ORG, params={"path": "x.md", "mount": "resident-regin"}
    )

    assert "resident-regin" not in names
    assert page.status_code == 404
    assert directory.calls == 0


def test_resident_memory_without_mount_auth_is_fatal(tmp_path):
    with pytest.raises(ValueError, match="mount_auth"):
        MimirRouter(
            MarkdownMimirAdapter(root=tmp_path),
            auth=EnvoyHeaderAuthenticationAdapter(),
            resident_memory=Directory([]),
        )


async def test_valkyrie_directory_reads_the_served_list_with_its_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json=[
                {
                    "valkyrieId": "regin",
                    "name": "resident-regin",
                    "role": "local",
                    "url": "https://regin.test",
                }
            ],
        )

    directory = ValkyrieMemoryDirectory(
        base_url="http://ravn.test",
        auth_adapter="tests.test_mimir.unit.test_resident_mounts.StaticAuth",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    assert await directory.served() == [REGIN]
    assert seen == {"path": "/api/v1/ravn/valkyrie/memory", "auth": "Bearer directory"}


async def test_valkyrie_directory_raises_when_the_api_refuses():
    directory = ValkyrieMemoryDirectory(
        base_url="http://ravn.test",
        auth_adapter="tests.test_mimir.unit.test_resident_mounts.StaticAuth",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await directory.served()


def test_valkyrie_directory_needs_auth():
    with pytest.raises(ValueError, match="auth_adapter"):
        ValkyrieMemoryDirectory(base_url="http://ravn.test", auth_adapter="", timeout_seconds=5)


class StaticAuth:
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer directory"}

    def invalidate(self) -> bool:
        return False
