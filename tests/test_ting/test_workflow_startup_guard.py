from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.main import _assert_workflow_catalog_migrated


class _Pool:
    def __init__(self, count: int, updated_at: datetime | None) -> None:
        self.row = {"row_count": count, "max_updated_at": updated_at}

    async def fetchrow(self, _query: str):
        return self.row


class _Repository:
    requires_legacy_catalog_migration_guard = True

    def __init__(self, marker=None, workflows=None) -> None:
        self.marker = marker
        self.workflows = workflows or {}

    def read_migration_marker(self):
        return self.marker

    async def get_workflow(self, workflow_id):
        return self.workflows.get(workflow_id)


@pytest.mark.asyncio
async def test_startup_guard_allows_new_empty_database() -> None:
    await _assert_workflow_catalog_migrated(_Repository(), _Pool(0, None), catalog_path="/catalog")


@pytest.mark.asyncio
async def test_startup_guard_rejects_unmigrated_database() -> None:
    with pytest.raises(RuntimeError, match="migrate_workflows"):
        await _assert_workflow_catalog_migrated(
            _Repository(),
            _Pool(1, datetime.now(UTC)),
            catalog_path="/catalog",
        )


@pytest.mark.asyncio
async def test_startup_guard_requires_inventory_files_and_accepts_tombstones() -> None:
    updated_at = datetime.now(UTC)
    present_id = uuid4()
    deleted_id = uuid4()
    marker = {
        "source_count": 2,
        "source_max_updated_at": updated_at.isoformat(),
        "inventory_ids": [str(present_id), str(deleted_id)],
        "deleted_ids": [str(deleted_id)],
    }
    await _assert_workflow_catalog_migrated(
        _Repository(marker, {present_id: object()}),
        _Pool(2, updated_at),
        catalog_path="/catalog",
    )

    with pytest.raises(RuntimeError, match="unverified or changed"):
        await _assert_workflow_catalog_migrated(
            _Repository(marker),
            _Pool(2, updated_at),
            catalog_path="/catalog",
        )
