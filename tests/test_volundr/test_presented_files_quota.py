"""LEX-1514: the host's total quota for durable presented files.

A copy that would take the store past ``presented_files.max_total_bytes`` is refused
loudly (HTTP 507 naming the knob and the remedy) and counted; nothing is evicted, and
deleting a session frees its share.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from volundr.adapters.inbound.rest_presented_files import create_presented_files_router
from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.config import PresentedFilesConfig
from volundr.domain.presented_files import PresentedFileQuota, PresentedFileQuotaError

BASE = "http://thor.tail737f2a.ts.net:8380"


def _file_id(n: int) -> str:
    return f"pf_{n:032x}"


async def test_quota_admits_what_fits_and_refuses_loudly(tmp_path) -> None:
    store = LocalPresentedFileStore(root=str(tmp_path))
    quota = PresentedFileQuota(10)
    sid = uuid4()
    await quota.admit(store, sid, _file_id(1), 6)
    await store.put(sid, _file_id(1), name="a", mime="text/plain", data=b"123456")

    with pytest.raises(PresentedFileQuotaError) as refused:
        await quota.admit(store, sid, _file_id(2), 5)

    message = str(refused.value)
    assert "presented_files.max_total_bytes" in message
    assert "delete sessions with presented files" in message
    assert (refused.value.used, refused.value.size, refused.value.limit) == (6, 5, 10)
    assert quota.refusals == 1
    # Re-storing an id that is already stored adds nothing, so it is admitted.
    await quota.admit(store, sid, _file_id(1), 6)
    assert await quota.usage(store) == {
        "used_bytes": 6,
        "max_total_bytes": 10,
        "quota_refusals": 1,
    }


async def test_store_total_counts_every_session_and_ignores_strays(tmp_path) -> None:
    store = LocalPresentedFileStore(root=str(tmp_path))
    await store.put(uuid4(), _file_id(1), name="a", mime="m", data=b"abc")
    await store.put(uuid4(), _file_id(2), name="b", mime="m", data=b"defgh")
    (tmp_path / "not-a-session").mkdir()
    (tmp_path / "not-a-session" / "content").write_bytes(b"x" * 100)
    assert await store.total_bytes() == 8
    assert await LocalPresentedFileStore(root=str(tmp_path / "missing")).total_bytes() == 0


def test_quota_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        PresentedFileQuota(0)


def _client(tmp_path, quota: PresentedFileQuota):
    from identity.adapters.identity import AllowAllIdentityAdapter

    store = LocalPresentedFileStore(root=str(tmp_path))
    service = AsyncMock()
    service.get_session.return_value = object()
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_presented_files_router(
            store, service, public_base_url=BASE, max_bytes=1024, quota=quota
        )
    )
    return TestClient(app), store


def _put(client, sid, file_id: str, data: bytes):
    return client.put(
        f"/api/v1/forge/sessions/{sid}/files/presented/{file_id}",
        content=data,
        headers={"content-type": "text/plain"},
    )


async def test_route_refuses_the_copy_that_does_not_fit(tmp_path) -> None:
    quota = PresentedFileQuota(10)
    client, store = _client(tmp_path, quota)
    sid = uuid4()

    first = _put(client, sid, _file_id(1), b"123456")
    second = _put(client, sid, _file_id(2), b"12345")
    again = _put(client, sid, _file_id(1), b"123456")

    assert first.status_code == 200, first.text
    assert second.status_code == 507
    detail = second.json()["detail"]
    assert detail["code"] == "presented_files_quota"
    assert "presented_files.max_total_bytes" in detail["message"]
    assert quota.refusals == 1
    assert await store.get(sid, _file_id(2)) is None  # nothing half-written
    assert again.status_code == 200  # an earlier file is still there and still accepted
    assert await store.get(sid, _file_id(1)) is not None


async def test_deleting_a_session_frees_its_share(tmp_path) -> None:
    quota = PresentedFileQuota(10)
    client, store = _client(tmp_path, quota)
    old, new = uuid4(), uuid4()
    assert _put(client, old, _file_id(1), b"12345678").status_code == 200
    assert _put(client, new, _file_id(2), b"12345").status_code == 507

    await store.delete_session(old)

    assert _put(client, new, _file_id(2), b"12345").status_code == 200
    assert await quota.usage(store) == {
        "used_bytes": 5,
        "max_total_bytes": 10,
        "quota_refusals": 1,
    }


def test_config_requires_a_quota_when_enabled() -> None:
    with pytest.raises(ValidationError, match="presented_files.max_total_bytes"):
        PresentedFilesConfig(enabled=True)
    assert PresentedFilesConfig(enabled=True, max_total_bytes=5 << 30).max_total_bytes == 5 << 30
    assert PresentedFilesConfig().max_total_bytes is None  # off: nothing to bound
    with pytest.raises(ValidationError):
        PresentedFilesConfig(enabled=True, max_total_bytes=0)
