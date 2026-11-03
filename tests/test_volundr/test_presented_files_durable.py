"""F2: Forge keeps a durable copy of every presented file and serves it after the session.

The phone opens a presented file by its absolute URL on the host's tailnet name
(``Artifact.url``: http(s), never ``*.xteo.mesh``); that URL must keep working after
the session's broker is gone.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_presented_files import create_presented_files_router
from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.config import LocalMountsConfig, PresentedFilesConfig
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.presented_files import (
    PresentedFileConflictError,
    PresentedFileQuota,
    durable_file_url,
    presented_file_path,
    validate_public_base_url,
)
from volundr.domain.services import SessionService

FILE_ID = "pf_" + "a" * 32
BASE = "http://thor.tail737f2a.ts.net:8380"


# ------------------------------------------------------------------ URL rules


def test_durable_url_is_absolute_on_the_tailnet_name():
    sid = uuid4()
    assert durable_file_url(BASE + "/", sid, FILE_ID) == (
        f"{BASE}/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    )
    assert durable_file_url("", sid, FILE_ID) is None
    assert (
        presented_file_path(sid, FILE_ID)
        == f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "http://thor.xteo.mesh:8380",
        "https://XTEO.MESH",
        "ftp://thor.tail737f2a.ts.net",
        "thor.tail737f2a.ts.net:8380",
        "http://thor.tail737f2a.ts.net:8380/?x=1",
        "https://forge.example.com",
        "http://thor:8080",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://192.168.1.20:8080",
        "http://8.8.8.8",
        "http://ts.net",
        "http://evil.ts.net.example.com",
        "http://[2001:db8::1]:8080",
    ],
)
def test_base_url_refuses_mesh_names_and_non_http(bad):
    with pytest.raises(ValueError):
        validate_public_base_url(bad)
    with pytest.raises(ValueError):
        PresentedFilesConfig(public_base_url=bad)


@pytest.mark.parametrize(
    "good",
    [
        "http://thor.tail737f2a.ts.net:8080",
        "https://THOR.tail737f2a.ts.net",
        "http://100.66.123.128:8080",
        "http://100.127.255.254",
        "http://[fd7a:115c:a1e0::1]:8080",
    ],
)
def test_base_url_accepts_only_tailnet_hosts(good):
    assert validate_public_base_url(good) == good
    assert PresentedFilesConfig(public_base_url=good).public_base_url == good


def test_config_defaults():
    config = PresentedFilesConfig()
    assert config.enabled is False and config.public_base_url == ""
    assert config.store["adapter"].endswith("LocalPresentedFileStore")
    assert PresentedFilesConfig(public_base_url=BASE + "/").public_base_url == BASE


# ------------------------------------------------------------------ the store


async def test_store_roundtrip_is_idempotent_and_refuses_other_bytes(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path))
    sid = uuid4()
    first = await store.put(sid, FILE_ID, name="report.pdf", mime="application/pdf", data=b"%PDF")
    again = await store.put(sid, FILE_ID, name="report.pdf", mime="application/pdf", data=b"%PDF")
    assert first == again and first.size == 4
    record, path = await store.get(sid, FILE_ID)
    assert record.name == "report.pdf" and path.read_bytes() == b"%PDF"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    with pytest.raises(PresentedFileConflictError):
        await store.put(sid, FILE_ID, name="x", mime="text/plain", data=b"other")
    assert await store.get(uuid4(), FILE_ID) is None
    assert await store.get(sid, "../../etc/passwd") is None
    with pytest.raises(ValueError):
        await store.put(sid, "pf_bad", name="x", mime="text/plain", data=b"")


async def test_store_ignores_symlinked_content(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path / "store"))
    sid = uuid4()
    await store.put(sid, FILE_ID, name="a.txt", mime="text/plain", data=b"a")
    content = tmp_path / "store" / str(sid) / FILE_ID / "content"
    content.unlink()
    secret = tmp_path / "secret"
    secret.write_text("no")
    os.symlink(secret, content)
    assert await store.get(sid, FILE_ID) is None


async def test_store_ignores_corrupt_metadata(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path))
    sid = uuid4()
    await store.put(sid, FILE_ID, name="a.txt", mime="text/plain", data=b"a")
    (tmp_path / str(sid) / FILE_ID / "meta.json").write_text("{")
    assert await store.get(sid, FILE_ID) is None


# ------------------------------------------------------------------ upload route


def _upload_client(tmp_path, *, session=object(), max_bytes=1024, max_total_bytes=1 << 30):
    from identity.adapters.identity import AllowAllIdentityAdapter

    store = LocalPresentedFileStore(root=str(tmp_path))
    service = AsyncMock()
    service.get_session.return_value = session
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_presented_files_router(
            store,
            service,
            public_base_url=BASE,
            max_bytes=max_bytes,
            quota=PresentedFileQuota(max_total_bytes),
        )
    )
    return TestClient(app), store, service


async def test_upload_stores_and_answers_the_durable_url(tmp_path):
    client, store, service = _upload_client(tmp_path)
    sid = uuid4()
    resp = client.put(
        f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}",
        content=b"%PDF-1.4",
        headers={"content-type": "application/pdf", "x-presented-file-name": "My%20report.pdf"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == f"{BASE}/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    assert body["name"] == "My report.pdf" and body["mime"] == "application/pdf"
    assert body["size"] == 8
    record, path = await store.get(sid, FILE_ID)
    assert path.read_bytes() == b"%PDF-1.4"
    service._check_access.assert_awaited()
    # A retry with the same bytes is fine; other bytes are a conflict.
    url = f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    assert client.put(url, content=b"%PDF-1.4").status_code == 200
    assert client.put(url, content=b"changed").status_code == 409


def test_upload_limits(tmp_path):
    client, _, _ = _upload_client(tmp_path, max_bytes=4)
    sid = uuid4()
    url = f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    assert client.put(url, content=b"12345").status_code == 413
    assert (
        client.put(f"/api/v1/forge/sessions/{sid}/files/presented/pf_x", content=b"1").status_code
        == 422
    )
    client, _, _ = _upload_client(tmp_path, session=None)
    assert client.put(url, content=b"1").status_code == 404


def test_chunked_upload_over_the_limit_is_refused(tmp_path):
    client, _, _ = _upload_client(tmp_path, max_bytes=4)

    def body():
        yield b"123"
        yield b"456"

    resp = client.put(f"/api/v1/forge/sessions/{uuid4()}/files/presented/{FILE_ID}", content=body())
    assert resp.status_code == 413


def test_upload_name_cannot_carry_a_path(tmp_path):
    client, _, _ = _upload_client(tmp_path)
    resp = client.put(
        f"/api/v1/forge/sessions/{uuid4()}/files/presented/{FILE_ID}",
        content=b"x",
        headers={"x-presented-file-name": "..%2F..%2Fetc%2Fpasswd"},
    )
    assert resp.json()["name"] == ".._.._etc_passwd"


# ------------------------------------------------------------------ serving after the session


def _forge_client(store):
    repository = InMemorySessionRepository()
    session_service = SessionService(
        repository=repository,
        pod_manager=MockPodManager(wait_for_ready_result=SessionStatus.RUNNING),
        validate_repos=False,
    )
    app = FastAPI()
    app.include_router(
        create_router(
            session_service,
            session_participant_service=make_session_participant_service(session_service),
            presented_file_store=store,
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app), repository


async def test_durable_copy_serves_after_the_session_stopped(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path))
    client, repository = _forge_client(store)
    session = Session(
        id=uuid4(),
        name="stopped",
        model="test",
        source=GitSource(repo="https://example.test/repo", branch="main"),
        status=SessionStatus.STOPPED,
    )
    await repository.create(session)
    url = presented_file_path(session.id, FILE_ID)

    # No durable copy and no live broker: nothing to serve.
    assert client.get(url).status_code == 404

    await store.put(session.id, FILE_ID, name="report.pdf", mime="application/pdf", data=b"%PDF")
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.content == b"%PDF"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    # A PDF keeps nosniff; the sandbox would stop Chrome's viewer, and a PDF can't run script here.
    assert "content-security-policy" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "report.pdf" in resp.headers["content-disposition"]
    # Agent-supplied HTML renders but never runs script on Forge's origin.
    html_id = "pf_" + "c" * 32
    await store.put(session.id, html_id, name="page.html", mime="text/html", data=b"<script>")
    html = client.get(presented_file_path(session.id, html_id))
    assert html.headers["content-security-policy"] == "sandbox"
    assert html.headers["x-content-type-options"] == "nosniff"

    # Another session's URL never reaches this copy.
    assert client.get(presented_file_path(uuid4(), FILE_ID)).status_code == 404


# ------------------------------------------------------------------ F1 records F2's URL


@pytest.mark.parametrize(
    ("url", "recorded"),
    [
        (f"{BASE}/api/v1/forge/sessions/s/files/presented/{FILE_ID}", "durable"),
        ("http://thor.xteo.mesh:8380/x", "route"),
        ("javascript:alert(1)", "route"),
        (None, "route"),
    ],
)
def test_publication_records_the_durable_url(url, recorded):
    from dataclasses import asdict
    from datetime import UTC, datetime

    from skuld.broker_api import _build_present_file_turn
    from tests.support.artifacts import CATALOG
    from volundr.domain.artifacts import publications_from_entry
    from volundr.domain.models import SessionLogEntry

    sid = uuid4()
    turn = asdict(_build_present_file_turn(FILE_ID, "plan.md", "text/markdown", 3, None, url=url))
    entry = SessionLogEntry(
        session_id=sid,
        seq=7,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=datetime(2026, 10, 22, tzinfo=UTC),
    )
    [pub] = publications_from_entry(entry, CATALOG)
    route = f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}"
    assert pub.url == (url if recorded == "durable" else route)


async def test_upload_landing_after_the_session_was_deleted_leaves_nothing(tmp_path):
    """The session is deleted while the body streams: its cleanup already ran, so the
    PUT removes what it wrote and answers 404 (the broker then stops retrying)."""
    client, store, service = _upload_client(tmp_path)
    service.get_session.side_effect = [object(), None]
    sid = uuid4()
    resp = client.put(f"/api/v1/forge/sessions/{sid}/files/presented/{FILE_ID}", content=b"x")
    assert resp.status_code == 404
    assert await store.get(sid, FILE_ID) is None
    assert not (tmp_path / str(sid)).exists()


# ------------------------------------------------------- judge R16-1: the safety policy


@respx.mock
async def test_live_broker_fallback_carries_the_same_safety_policy(tmp_path):
    """No durable copy yet: the live broker's staged copy is proxied, and Forge adds the
    sandbox and nosniff headers itself (the broker sends none) and never caches it."""
    client, repository = _forge_client(LocalPresentedFileStore(root=str(tmp_path)))
    session = Session(
        id=uuid4(),
        name="live",
        model="test",
        source=GitSource(repo="https://example.test/repo", branch="main"),
        status=SessionStatus.RUNNING,
        chat_endpoint="ws://broker.test:8080/session",
        code_endpoint="http://broker.test:8080/",
    )
    await repository.create(session)
    respx.route(path__regex=r".*/api/files/presented/.*").mock(
        return_value=httpx.Response(
            200, content=b"<script>x</script>", headers={"content-type": "text/html"}
        )
    )
    resp = client.get(presented_file_path(session.id, FILE_ID))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-security-policy"] == "sandbox"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "private, no-store"
