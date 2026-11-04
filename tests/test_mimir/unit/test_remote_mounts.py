"""Operator-declared remote mounts: a store another service serves, mounted here."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.config import RemoteMountConfig
from mimir.router import MimirRouter
from ravn.adapters.channels.gateway import RavnGateway
from ravn.adapters.channels.gateway_http import HttpGateway
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.config import HttpChannelConfig
from ravn.domain.mimir import MimirAuth


def identity(tenant):
    return {"x-auth-user-id": "user", "x-auth-tenant": tenant, "x-auth-roles": "volundr:admin"}


def _remote(name, port, tenant_id=""):
    return {
        "name": name,
        "tenant_id": tenant_id,
        "role": "local",
        "priority": 10,
        "categories": None,
        "port": port,
    }


def _client(tmp_path, remote_mounts):
    app = FastAPI()
    app.include_router(
        MimirRouter(
            MarkdownMimirAdapter(root=tmp_path / "shared"),
            name="shared",
            auth=EnvoyHeaderAuthenticationAdapter(),
            remote_mounts=remote_mounts,
        ).router
    )
    return TestClient(app)


async def test_a_tenant_remote_mount_is_listed_and_read_by_that_tenant_only(tmp_path):
    regin = MarkdownMimirAdapter(root=tmp_path / "regin")
    await regin.upsert_page("learnings/release.md", "# Release\n\nShip on Thursdays.\n")
    client = _client(tmp_path, [_remote("regin", regin, tenant_id="default")])

    names = [m["name"] for m in client.get("/mounts", headers=identity("default")).json()]
    page = client.get(
        "/page",
        headers=identity("default"),
        params={"path": "learnings/release.md", "mount": "regin"},
    )
    other = client.get(
        "/page",
        headers=identity("acme"),
        params={"path": "learnings/release.md", "mount": "regin"},
    )

    assert "regin" in names
    assert page.status_code == 200
    assert "Ship on Thursdays" in page.json()["content"]
    assert other.status_code == 404


def test_a_global_remote_mount_stays_out_of_other_orgs(tmp_path):
    client = _client(tmp_path, [_remote("regin", MagicMock())])

    names = [m["name"] for m in client.get("/mounts", headers=identity("acme")).json()]

    assert "regin" not in names


def test_a_remote_mount_named_like_another_mount_is_refused(tmp_path):
    client = _client(tmp_path, [_remote("shared", MagicMock())])

    with pytest.raises(ValueError, match="Remote mount shared conflicts"):
        client.get("/mounts")


def test_remote_mount_config_requires_an_adapter():
    with pytest.raises(ValueError):
        RemoteMountConfig(name="regin")


async def test_platform_mimir_reads_a_resident_store_through_its_gateway(tmp_path):
    """The whole path: platform Mímir → resident gateway → the resident's own store."""
    store = MarkdownMimirAdapter(root=tmp_path / "regin")
    await store.upsert_page("learnings/release.md", "# Release\n\nShip on Thursdays.\n")
    verifier = MagicMock()
    verifier.verify = AsyncMock(
        side_effect=lambda token: {"workload_service": "mimir"} if token == "platform" else {}
    )
    gateway = HttpGateway(
        HttpChannelConfig(
            enabled=True,
            mimir_served_mount="local",
            mimir_required_claims={"workload_service": "mimir"},
        ),
        MagicMock(spec=RavnGateway),
        mimir=store,
        mimir_verifier=verifier,
    )
    remote = HttpMimirAdapter(
        base_url="https://regin.valhalla.test",
        auth=MimirAuth(type="bearer", token="platform"),
    )
    remote._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="https://regin.valhalla.test"
    )
    client = _client(tmp_path, [_remote("regin", remote, tenant_id="default")])

    page = client.get(
        "/page",
        headers=identity("default"),
        params={"path": "learnings/release.md", "mount": "regin"},
    )

    assert page.status_code == 200
    assert "Ship on Thursdays" in page.json()["content"]
    verifier.verify.assert_awaited_with("platform")


async def test_the_service_mounts_what_its_config_declares(tmp_path):
    from mimir.app import create_app
    from mimir.config import MimirServiceConfig

    regin = MarkdownMimirAdapter(root=tmp_path / "regin")
    await regin.upsert_page("learnings/release.md", "# Release\n\nShip on Thursdays.\n")
    app = create_app(
        MimirServiceConfig(
            path=str(tmp_path / "shared"),
            # As the chart renders it: plain mappings, read from config.yaml.
            remote_mounts=[
                {
                    "name": "regin",
                    "tenant_id": "default",
                    "adapter": "mimir.adapters.markdown.MarkdownMimirAdapter",
                    "kwargs": {"root": str(tmp_path / "regin")},
                }
            ],
        )
    )
    with TestClient(app) as client:
        page = client.get(
            "/mimir/page",
            headers=identity("default"),
            params={"path": "learnings/release.md", "mount": "regin"},
        )

    assert page.status_code == 200
    assert "Ship on Thursdays" in page.json()["content"]
