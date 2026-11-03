"""F2 through the Guild facade (what RootServer serves on /api/v1/forge): the broker's
durable upload is a PUT that must reach the embedded Forge with its session credential,
and the durable copy then serves through the same facade after the session stops."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.domain.presented_files import PRESENTED_FILE_NAME_HEADER
from tests.support.forge_session import OWNER, TENANT, bearer, build_forge_app
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance
from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.domain.models import SessionStatus

FID = "pf_" + "a" * 32
BASE = "http://thor.tail737f2a.ts.net:8380"
OWNER_HEADERS = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}


@pytest.fixture
async def guild(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path))
    forge = build_forge_app(presented_file_store=store)
    local = _instance(
        "local",
        base_url="embedded://local-forge",
        tenant_id=TENANT,
        is_default=True,
        config={"transport": "embedded"},
    )
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(
            StubInstanceService([local]), embedded_forge_app=forge.app, presented_file_max_bytes=16
        )
    )
    session = await forge.session("self")
    credential = await forge.start(session)
    return TestClient(app), forge, session, bearer(credential.token), store


def _url(session_id, fid=FID) -> str:
    return f"/api/v1/forge/sessions/{session_id}/files/presented/{fid}"


async def test_broker_upload_through_the_facade_then_served_after_stop(guild) -> None:
    client, forge, session, auth, store = guild
    put = client.put(
        _url(session.id),
        content=b"hello durable",
        headers={
            **auth,
            "content-type": "text/plain",
            PRESENTED_FILE_NAME_HEADER: "my%20notes.txt",
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["url"] == f"{BASE}{_url(session.id)}"
    assert body["name"] == "my notes.txt" and body["mime"] == "text/plain"
    record, _ = await store.get(session.id, FID)
    assert record.size == len(b"hello durable")

    await forge.set_status(session.id, SessionStatus.STOPPED)
    # The owner reads it (the phone on the tailnet, as dev-user in mini mode); no broker is running.
    got = client.get(_url(session.id), headers=OWNER_HEADERS)
    assert got.status_code == 200
    assert got.content == b"hello durable"
    assert got.headers["content-type"].startswith("text/plain")


async def test_forge_refusals_pass_through_unchanged(guild) -> None:
    client, forge, session, auth, _ = guild
    assert client.put(_url(session.id), content=b"one", headers=auth).status_code == 200
    assert client.put(_url(session.id), content=b"two", headers=auth).status_code == 409
    peer = await forge.session("peer")
    assert client.put(_url(peer.id), content=b"x", headers=auth).status_code == 403


async def test_the_facade_bounds_the_body_before_buffering_it(guild) -> None:
    client, _, session, auth, store = guild
    assert client.put(_url(session.id), content=b"x" * 17, headers=auth).status_code == 413

    def chunks():
        yield b"x" * 10
        yield b"x" * 10

    streamed = client.put(_url(session.id), content=chunks(), headers=auth)  # no content-length
    assert streamed.status_code == 413
    assert await store.get(session.id, FID) is None


async def test_a_bad_id_or_an_unknown_session_is_refused(guild) -> None:
    client, _, session, auth, _ = guild
    assert client.put(
        _url(session.id, "pf_..%2f..%2fx"), content=b"x", headers=auth
    ).status_code in (404, 422)
    assert client.put(_url(session.id, "not-an-id"), content=b"x", headers=auth).status_code == 422
    unknown = "00000000-0000-4000-8000-000000000099"
    assert client.put(_url(unknown), content=b"x", headers=auth).status_code in (403, 404)


async def test_the_public_get_keeps_the_sandbox(guild) -> None:
    """Judge R16-1's reproducer: an agent-supplied HTML file through the PUBLIC facade keeps
    Content-Security-Policy: sandbox, nosniff and the durable cache policy, like the node."""
    client, forge, session, _, store = guild
    await store.put(session.id, FID, name="audit.html", mime="text/html", data=b"<p>audit</p>")
    direct = forge.client.get(_url(session.id), headers=OWNER_HEADERS)
    public = client.get(_url(session.id), headers=OWNER_HEADERS)
    assert public.status_code == direct.status_code == 200
    assert public.content == direct.content
    for name in (
        "content-security-policy",
        "x-content-type-options",
        "cache-control",
        "content-disposition",
    ):
        assert public.headers.get(name) == direct.headers.get(name), name
    assert public.headers["content-security-policy"] == "sandbox"
    assert public.headers["x-content-type-options"] == "nosniff"


async def test_the_facade_enforces_the_policy_even_if_the_node_omits_it() -> None:
    """Defence in depth: a node (an older Forge) that answers without the headers still gets
    them added by the facade, with no caching."""
    node = FastAPI()

    @node.get("/api/v1/forge/sessions/{sid}/files/presented/{fid}")
    async def bare(sid: str, fid: str):
        # An old or wrong node: no safety headers, and a cache policy the facade must not trust.
        return Response(
            content=b"<svg onload=alert(1)>",
            media_type="image/svg+xml",
            headers={"cache-control": "public, max-age=31536000"},
        )

    @node.get("/api/v1/forge/sessions/{sid}")
    async def session_row(sid: str):
        return {"id": sid, "name": "s", "owner_id": OWNER, "tenant_id": TENANT}

    local = _instance(
        "local",
        base_url="embedded://n",
        tenant_id=TENANT,
        is_default=True,
        config={"transport": "embedded"},
    )
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(create_volundr_router(StubInstanceService([local]), embedded_forge_app=node))
    sid = "00000000-0000-4000-8000-000000000001"
    r = TestClient(app).get(
        f"/api/v1/forge/sessions/{sid}/files/presented/{FID}", headers=OWNER_HEADERS
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, no-store"


def test_the_policy_by_type() -> None:
    from niuu.domain.presented_files import presented_file_safety_headers

    assert presented_file_safety_headers("text/html; charset=utf-8") == {
        "x-content-type-options": "nosniff",
        "content-security-policy": "sandbox",
    }
    assert presented_file_safety_headers("image/svg+xml")["content-security-policy"] == "sandbox"
    assert presented_file_safety_headers("application/pdf") == {"x-content-type-options": "nosniff"}
    assert presented_file_safety_headers(None)["content-security-policy"] == "sandbox"
