"""Tests for LocalStorageAdapter."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from volundr.adapters.outbound.local_storage_adapter import LocalStorageAdapter
from volundr.domain.models import StorageQuota, WorkspaceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(base_dir=str(tmp_path))


@pytest.fixture
def quota() -> StorageQuota:
    return StorageQuota(home_gb=1, workspace_gb=1)


# ------------------------------------------------------------------
# provision_user_storage
# ------------------------------------------------------------------


async def test_provision_user_storage_creates_dir(
    adapter: LocalStorageAdapter, tmp_path: Path, quota: StorageQuota
) -> None:
    ref = await adapter.provision_user_storage("user-1", quota)
    assert (tmp_path / "home" / "user-1").is_dir()
    assert ref.namespace == "local"
    assert ref.name == str(tmp_path / "home" / "user-1")


async def test_provision_user_storage_idempotent(
    adapter: LocalStorageAdapter, quota: StorageQuota
) -> None:
    ref1 = await adapter.provision_user_storage("user-1", quota)
    ref2 = await adapter.provision_user_storage("user-1", quota)
    assert ref1 == ref2


# ------------------------------------------------------------------
# create_session_workspace
# ------------------------------------------------------------------


async def test_create_session_workspace(adapter: LocalStorageAdapter, tmp_path: Path) -> None:
    ref = await adapter.create_session_workspace("sess-1", "user-1", "tenant-1")
    ws_dir = tmp_path / "workspaces" / "sess-1"
    assert ws_dir.is_dir()
    meta_path = ws_dir / ".volundr-meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["session_id"] == "sess-1"
    assert meta["user_id"] == "user-1"
    assert meta["tenant_id"] == "tenant-1"
    assert meta["status"] == "active"
    assert ref.namespace == "local"


async def test_create_session_workspace_returns_pvcref_with_path(
    adapter: LocalStorageAdapter, tmp_path: Path
) -> None:
    ref = await adapter.create_session_workspace("sess-2", "user-1")
    expected = str(tmp_path / "workspaces" / "sess-2")
    assert ref.name == expected
    assert Path(ref.name).is_absolute()


async def test_create_session_workspace_stores_name_and_source(
    adapter: LocalStorageAdapter, tmp_path: Path
) -> None:
    await adapter.create_session_workspace(
        "sess-3",
        "user-1",
        "tenant-1",
        name="my-project",
        source_url="github.com/org/repo",
        source_ref="main",
    )
    meta_path = tmp_path / "workspaces" / "sess-3" / ".volundr-meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["name"] == "my-project"
    assert meta["source_url"] == "github.com/org/repo"
    assert meta["source_ref"] == "main"


# ------------------------------------------------------------------
# archive_session_workspace
# ------------------------------------------------------------------


async def test_archive_session_workspace(adapter: LocalStorageAdapter, tmp_path: Path) -> None:
    await adapter.create_session_workspace("sess-1", "user-1", "tenant-1")
    await adapter.archive_session_workspace("sess-1")
    meta_path = tmp_path / "workspaces" / "sess-1" / ".volundr-meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "archived"


# ------------------------------------------------------------------
# delete_workspace
# ------------------------------------------------------------------


async def test_delete_workspace_raises_for_local_storage(
    adapter: LocalStorageAdapter, tmp_path: Path
) -> None:
    await adapter.create_session_workspace("sess-1", "user-1")
    ws_dir = tmp_path / "workspaces" / "sess-1"
    assert ws_dir.exists()
    with pytest.raises(RuntimeError, match="locally mounted workspace"):
        await adapter.delete_workspace("sess-1")
    # Directory must still exist — local storage is never deleted
    assert ws_dir.exists()


# ------------------------------------------------------------------
# list / get helpers (via internal state)
# ------------------------------------------------------------------


async def test_list_workspaces_all(adapter: LocalStorageAdapter) -> None:
    await adapter.create_session_workspace("s1", "u1")
    await adapter.create_session_workspace("s2", "u2")
    await adapter.create_session_workspace("s3", "u1")
    assert len(adapter._session_workspaces) == 3


async def test_list_workspaces_by_user(adapter: LocalStorageAdapter) -> None:
    await adapter.create_session_workspace("s1", "u1")
    await adapter.create_session_workspace("s2", "u2")
    await adapter.create_session_workspace("s3", "u1")
    entries = [e for e in adapter._session_workspaces.values() if e.user_id == "u1"]
    assert len(entries) == 2


async def test_list_workspaces_by_status(adapter: LocalStorageAdapter) -> None:
    await adapter.create_session_workspace("s1", "u1")
    await adapter.create_session_workspace("s2", "u1")
    await adapter.archive_session_workspace("s1")
    archived = [
        e for e in adapter._session_workspaces.values() if e.status == WorkspaceStatus.ARCHIVED
    ]
    active = [e for e in adapter._session_workspaces.values() if e.status == WorkspaceStatus.ACTIVE]
    assert len(archived) == 1
    assert len(active) == 1


# ------------------------------------------------------------------
# get_workspace_by_session (internal lookup)
# ------------------------------------------------------------------


async def test_get_workspace_by_session(adapter: LocalStorageAdapter) -> None:
    await adapter.create_session_workspace("sess-1", "user-1")
    entry = adapter._session_workspaces.get("sess-1")
    assert entry is not None
    assert entry.user_id == "user-1"


async def test_get_workspace_by_session_nonexistent(
    adapter: LocalStorageAdapter,
) -> None:
    entry = adapter._session_workspaces.get("no-such")
    assert entry is None


async def test_resolve_session_workspace_path_reconstructs_from_configured_base(
    adapter: LocalStorageAdapter,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspaces" / "sess-lookup"
    workspace.mkdir(parents=True)

    assert adapter.resolve_session_workspace_path("sess-lookup") == str(workspace)


# ------------------------------------------------------------------
# get_user_storage_usage
# ------------------------------------------------------------------


async def test_get_user_storage_usage(adapter: LocalStorageAdapter) -> None:
    await adapter.create_session_workspace("s1", "u1", workspace_gb=10)
    await adapter.create_session_workspace("s2", "u1", workspace_gb=20)
    await adapter.create_session_workspace("s3", "u2", workspace_gb=5)
    assert await adapter.get_user_storage_usage("u1") == 30
    assert await adapter.get_user_storage_usage("u2") == 5
    assert await adapter.get_user_storage_usage("unknown") == 0


# ------------------------------------------------------------------
# deprovision_user_storage
# ------------------------------------------------------------------


async def test_deprovision_user_storage(
    adapter: LocalStorageAdapter, tmp_path: Path, quota: StorageQuota
) -> None:
    await adapter.provision_user_storage("user-1", quota)
    assert (tmp_path / "home" / "user-1").is_dir()
    await adapter.deprovision_user_storage("user-1")
    assert not (tmp_path / "home" / "user-1").exists()


# ------------------------------------------------------------------
# scan_existing_on_init (persistence across restarts)
# ------------------------------------------------------------------


async def test_scan_existing_on_init(tmp_path: Path) -> None:
    # Pre-create workspace dirs with meta files
    ws_dir = tmp_path / "workspaces" / "sess-old"
    ws_dir.mkdir(parents=True)
    now = datetime.datetime.now(datetime.UTC)
    meta = {
        "session_id": "sess-old",
        "user_id": "user-x",
        "tenant_id": "tenant-x",
        "status": "active",
        "size_gb": 10,
        "created_at": now.isoformat(),
    }
    (ws_dir / ".volundr-meta.json").write_text(json.dumps(meta))

    # Pre-create home dir
    home_dir = tmp_path / "home" / "user-x"
    home_dir.mkdir(parents=True)

    # Construct new adapter -- should pick up existing state
    adapter = LocalStorageAdapter(base_dir=str(tmp_path))

    assert "sess-old" in adapter._session_workspaces
    entry = adapter._session_workspaces["sess-old"]
    assert entry.user_id == "user-x"
    assert entry.status == WorkspaceStatus.ACTIVE
    assert entry.size_gb == 10

    assert "user-x" in adapter._user_pvcs
    assert adapter._user_pvcs["user-x"].namespace == "local"
