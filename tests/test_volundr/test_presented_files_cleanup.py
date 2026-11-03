"""F2: a session's durable presented files go when the session is deleted."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.domain.models import GitSource
from volundr.domain.services import SessionService

FID_A = "pf_" + "a" * 32
FID_B = "pf_" + "b" * 32


async def test_store_deletes_only_that_sessions_files(tmp_path):
    store = LocalPresentedFileStore(root=str(tmp_path))
    gone, kept = uuid4(), uuid4()
    await store.put(gone, FID_A, name="a.txt", mime="text/plain", data=b"a")
    await store.put(gone, FID_B, name="b.txt", mime="text/plain", data=b"b")
    await store.put(kept, FID_A, name="a.txt", mime="text/plain", data=b"a")

    assert await store.delete_session(gone) == 2
    assert await store.get(gone, FID_A) is None
    assert not (tmp_path / str(gone)).exists()
    assert await store.get(kept, FID_A) is not None
    # Deleting again, or a session that never presented anything, is a no-op.
    assert await store.delete_session(gone) == 0
    assert await store.delete_session(uuid4()) == 0


async def test_store_never_follows_a_symlinked_session_dir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    root = tmp_path / "root"
    root.mkdir()
    sid = uuid4()
    (root / str(sid)).symlink_to(outside, target_is_directory=True)

    assert await LocalPresentedFileStore(root=str(root)).delete_session(sid) == 0
    assert (outside / "precious").read_text() == "keep"
    assert not (root / str(sid)).exists()


async def _make_session(service):
    return await service.create_session(
        name="presented",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
    )


@pytest.fixture
def store(tmp_path):
    return LocalPresentedFileStore(root=str(tmp_path / "presented"))


async def test_deleting_a_session_removes_its_presented_files(repository, pod_manager, store):
    service = SessionService(
        repository=repository,
        pod_manager=pod_manager,
        presented_file_store=store,
        provisioning_initial_delay=0,
    )
    session = await _make_session(service)
    await store.put(session.id, FID_A, name="r.pdf", mime="application/pdf", data=b"%PDF")

    assert await service.delete_session(session.id) is True
    assert await store.get(session.id, FID_A) is None


async def test_store_failure_does_not_block_the_delete(repository, pod_manager, caplog):
    class BrokenStore(LocalPresentedFileStore):
        async def delete_session(self, session_id):
            raise OSError("disk gone")

    service = SessionService(
        repository=repository,
        pod_manager=pod_manager,
        presented_file_store=BrokenStore(root="/nonexistent"),
        provisioning_initial_delay=0,
    )
    session = await _make_session(service)
    with caplog.at_level(logging.WARNING):
        assert await service.delete_session(session.id) is True
    assert "Failed to remove presented files" in caplog.text
    assert await repository.get(session.id) is None


async def test_no_store_configured_deletes_as_before(repository, pod_manager):
    service = SessionService(repository=repository, pod_manager=pod_manager)
    session = await _make_session(service)
    assert await service.delete_session(session.id) is True


async def test_files_go_even_when_a_later_cleanup_step_fails(repository, pod_manager, store):
    spans = AsyncMock()
    spans.delete_by_session.side_effect = RuntimeError("spans down")
    service = SessionService(
        repository=repository,
        pod_manager=pod_manager,
        presented_file_store=store,
        span_repository=spans,
        provisioning_initial_delay=0,
    )
    session = await _make_session(service)
    await store.put(session.id, FID_A, name="r.pdf", mime="application/pdf", data=b"%PDF")
    with pytest.raises(RuntimeError):
        await service.delete_session(session.id)
    assert await store.get(session.id, FID_A) is None
