"""Fakes for the memory import tests: the tracker, the gateway, the compile run and the records."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ting.domain.memory_imports import (
    ImportConnectionError,
    IssueComment,
    MemoryImport,
    MemoryImportExistsError,
    SourceAccepted,
    SourceGatewayError,
    SourceWrite,
    UpdatedIssue,
)
from ting.ports.memory_imports import (
    CompileLauncher,
    ImportConnections,
    IssueUpdateReader,
    MemoryImportRepository,
    MemorySourceGateway,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
BASE_TIME = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def issue(
    number: int,
    *,
    minutes: int,
    description: str = "",
    comments: tuple[IssueComment, ...] = (),
    url: str | None = None,
) -> UpdatedIssue:
    """LIN-<number>, last changed ``minutes`` after BASE_TIME."""
    identifier = f"LIN-{number}"
    return UpdatedIssue(
        id=f"uuid-{number}",
        identifier=identifier,
        title=f"Ticket {number}",
        description=description or f"What ticket {number} is about",
        status="In Progress",
        assignee="Ines Moreau",
        labels=("backend",),
        url=f"https://linear.app/niuu/issue/{identifier}" if url is None else url,
        created_at=BASE_TIME,
        updated_at=BASE_TIME + timedelta(minutes=minutes),
        comments=comments,
    )


class FakeTracker:
    """Linear as the import reads it: the tickets it holds now, filtered by ``updatedAt``."""

    def __init__(self, *issues: UpdatedIssue) -> None:
        self.issues = {item.identifier: item for item in issues}
        #: ``since`` of each read, in order.
        self.reads: list[datetime | None] = []
        self.fail: Exception | None = None
        self.closed = 0

    def put(self, item: UpdatedIssue) -> None:
        self.issues[item.identifier] = item

    def reader(self) -> IssueUpdateReader:
        tracker = self

        class Reader(IssueUpdateReader):
            async def issues_updated_since(self, tracker_project_id, since):  # noqa: ANN001
                tracker.reads.append(since)
                if tracker.fail is not None:
                    raise tracker.fail
                return [
                    item
                    for item in tracker.issues.values()
                    if since is None or item.updated_at > since
                ]

            async def close(self) -> None:
                tracker.closed += 1

        return Reader()


class FakeConnections(ImportConnections):
    def __init__(self, tracker: FakeTracker) -> None:
        self._tracker = tracker
        self.refusal: ImportConnectionError | None = None
        #: (project id, connection id, acting_for) of each reader asked for.
        self.asked: list[tuple[UUID, str, str]] = []

    async def reader(self, project_id, connection_id, *, acting_for):  # noqa: ANN001
        self.asked.append((project_id, connection_id, acting_for))
        if self.refusal is not None:
            raise self.refusal
        return self._tracker.reader()


class FakeGateway(MemorySourceGateway):
    """The gateway's rules for a source: the same content in a layer is not new."""

    def __init__(self) -> None:
        self.writes: list[SourceWrite] = []
        #: external ids the gateway refuses, with the error to raise.
        self.failing: dict[str, Exception] = {}
        self._known: dict[tuple[str, str, str], str] = {}

    async def write_source(self, source: SourceWrite) -> SourceAccepted:
        if source.external_id in self.failing:
            raise self.failing[source.external_id]
        self.writes.append(source)
        digest = hashlib.sha256(source.content.encode()).hexdigest()[:12]
        key = (source.layer, source.external_id, digest)
        new = key not in self._known
        if new:
            self._known[key] = f"src-{source.external_id}-{digest}"
        return SourceAccepted(source_id=self._known[key], layer=source.layer, new=new)

    def fail_with(self, external_id: str, message: str = "gateway is down") -> None:
        self.failing[external_id] = SourceGatewayError(message)


class FakeCompiler(CompileLauncher):
    def __init__(self) -> None:
        #: (project id, tenant, owner, source ids) of each run started.
        self.launched: list[tuple[UUID, str, str, list[str]]] = []
        self.error: Exception | None = None

    async def launch(self, *, project_id, tenant_id, owner_id, source_ids):  # noqa: ANN001
        if self.error is not None:
            raise self.error
        self.launched.append((project_id, tenant_id, owner_id, list(source_ids)))
        return f"run-{len(self.launched)}"


class MemoryRepository(MemoryImportRepository):
    def __init__(self) -> None:
        self.records: dict[UUID, MemoryImport] = {}

    async def create(self, record: MemoryImport) -> MemoryImport:
        for other in self.records.values():
            if (
                other.project_id,
                other.connector,
                other.connection_id,
                other.tracker_project_id,
            ) == (
                record.project_id,
                record.connector,
                record.connection_id,
                record.tracker_project_id,
            ):
                raise MemoryImportExistsError("already imported")
        self.records[record.id] = record
        return record

    async def get(self, import_id):  # noqa: ANN001
        return self.records.get(import_id)

    async def list_for_project(self, project_id):  # noqa: ANN001
        return [r for r in self.records.values() if r.project_id == project_id]

    async def list_all(self):
        return list(self.records.values())

    async def delete(self, import_id) -> bool:  # noqa: ANN001
        return self.records.pop(import_id, None) is not None

    async def record_progress(self, import_id, *, cursor, add_pending):  # noqa: ANN001
        record = self.records[import_id]
        candidates = [c for c in (record.cursor, cursor) if c is not None]
        moved = max(candidates) if candidates else None
        pending = tuple(dict.fromkeys([*record.pending_source_ids, *add_pending]))
        self.records[import_id] = _replace(record, cursor=moved, pending_source_ids=pending)

    async def clear_pending(self, import_id, source_ids: Sequence[str]) -> None:  # noqa: ANN001
        record = self.records[import_id]
        pending = tuple(s for s in record.pending_source_ids if s not in set(source_ids))
        self.records[import_id] = _replace(record, pending_source_ids=pending)

    async def record_run(self, import_id, *, ran_at, error: str) -> None:  # noqa: ANN001
        self.records[import_id] = _replace(
            self.records[import_id], last_run_at=ran_at, last_error=error
        )


def _replace(record: MemoryImport, **changes) -> MemoryImport:  # noqa: ANN003
    return replace(record, **changes)
