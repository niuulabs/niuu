"""Another org reaches its own mounts on a shared Mímir, never the host's knowledge."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.mcp import MimirMcpServer
from mimir.router import MimirRouter


def _identity(tenant: str) -> dict[str, str]:
    return {
        "x-auth-user-id": "alice",
        "x-auth-tenant": tenant,
        "x-auth-roles": "volundr:developer",
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    adapter = MarkdownMimirAdapter(root=tmp_path / "host")
    auth = EnvoyHeaderAuthenticationAdapter()
    app = FastAPI()
    app.include_router(MimirRouter(adapter, name="shared", auth=auth).router)
    app.include_router(MimirMcpServer(adapter, auth=auth).router(), prefix="/mcp")
    test_client = TestClient(app)
    written = test_client.put(
        "/page",
        headers=_identity("default"),
        json={"path": "technical/secret.md", "content": "# Operator knowledge"},
    )
    assert written.status_code == 204
    return test_client


def test_the_platform_org_reads_the_hosts_knowledge(client: TestClient) -> None:
    pages = client.get("/pages", headers=_identity("default")).json()

    assert [page["path"] for page in pages] == ["technical/secret.md"]


def test_another_org_finds_nothing_of_the_hosts(client: TestClient) -> None:
    headers = _identity("org-a")

    assert client.get("/mounts", headers=headers).json() == []
    assert client.get("/pages", headers=headers).json() == []
    assert client.get("/search", headers=headers, params={"q": "Operator"}).json() == []
    assert client.get("/stats", headers=headers).json()["page_count"] == 0
    response = client.get("/page", headers=headers, params={"path": "technical/secret.md"})
    assert response.status_code == 404


def test_another_org_cannot_name_the_hosts_mount(client: TestClient) -> None:
    response = client.get(
        "/page",
        headers=_identity("org-a"),
        params={"path": "technical/secret.md", "mount": "shared"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "route",
    ["/routing/rules", "/ravns/bindings", "/entities/index", "/doctor", "/activity/live"],
)
def test_host_only_routes_are_refused(client: TestClient, route: str) -> None:
    assert client.get(route, headers=_identity("org-a")).status_code == 403


def test_mcp_is_refused_because_it_reads_the_host_directly(client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mimir_read", "arguments": {"path": "technical/secret.md"}},
    }

    response = client.post("/mcp", headers=_identity("org-a"), json=body)

    assert response.status_code == 403
    assert "Operator knowledge" not in response.text


def test_an_unauthenticated_local_caller_keeps_the_hosts_knowledge(client: TestClient) -> None:
    """A warden behind no credential is the host itself, not another org."""
    assert [page["path"] for page in client.get("/pages").json()] == ["technical/secret.md"]
