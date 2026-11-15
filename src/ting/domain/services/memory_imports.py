"""Running memory imports: read what changed, hand it to the gateway, compile what is new.

The rules that make a run safe to repeat and loud when it fails:

* The cursor moves only over tickets the gateway accepted. A failure part-way moves it to
  the newest change time every earlier ticket of which was accepted, never past the ticket
  that failed, so the next run starts at the failure.
* A run that created sources and whose import compiles starts the compile workflow. The
  sources wait on the import record until a run was started for them, so a compile that
  could not start is retried by the next run instead of forgotten.
* A failed run records ``last_error`` on the import, logs at error and is counted. The loop
  runs the import again next interval; "run now" raises the same error to its caller.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.observability import get_observability
from ting.domain.memory_imports import (
    SOURCE_KIND_TICKET,
    SUPPORTED_CONNECTORS,
    ImportRunResult,
    MemoryImport,
    MemoryImportError,
    SourceWrite,
    UpdatedIssue,
    issue_source_content,
)
from ting.ports.memory_imports import (
    CompileLauncher,
    ImportConnections,
    MemoryImportRepository,
    MemorySourceGateway,
)

logger = logging.getLogger(__name__)

#: Counter of import runs that failed, attributed by connector.
IMPORT_FAILURES = "ting.memory.import.failures"
#: Counter of import runs that completed, attributed by connector.
IMPORT_RUNS = "ting.memory.import.runs"
#: Counter of sources the gateway created for an import, attributed by connector.
IMPORT_SOURCES = "ting.memory.import.sources"


class MemoryImportNotFoundError(MemoryImportError):
    """There is no such import."""


class MemoryImportService:
    def __init__(
        self,
        *,
        repository: MemoryImportRepository,
        connections: ImportConnections,
        gateway: MemorySourceGateway,
        compiler: CompileLauncher,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._gateway = gateway
        self._compiler = compiler
        self._clock = clock
        self._locks: dict[UUID, asyncio.Lock] = {}

    # -- records --

    async def create(
        self,
        *,
        project_id: UUID,
        tenant_id: str,
        owner_id: str,
        connector: str,
        connection_id: str,
        tracker_project_id: str,
        compile: bool,
    ) -> MemoryImport:
        """Record an import after checking that its connection is one it can read with."""
        if connector not in SUPPORTED_CONNECTORS:
            raise ValueError(
                f"connector {connector!r} is not supported; use one of {SUPPORTED_CONNECTORS}"
            )
        if not tracker_project_id.strip():
            raise ValueError("tracker_project_id is required")
        reader = await self._connections.reader(project_id, connection_id, acting_for=owner_id)
        await reader.close()
        return await self._repository.create(
            MemoryImport(
                id=uuid4(),
                project_id=project_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                connector=connector,
                connection_id=connection_id,
                tracker_project_id=tracker_project_id.strip(),
                compile=compile,
                cursor=None,
                created_at=self._clock(),
            )
        )

    async def get(self, import_id: UUID) -> MemoryImport | None:
        return await self._repository.get(import_id)

    async def list_for_project(self, project_id: UUID) -> list[MemoryImport]:
        return await self._repository.list_for_project(project_id)

    async def delete(self, import_id: UUID) -> bool:
        deleted = await self._repository.delete(import_id)
        self._locks.pop(import_id, None)
        return deleted

    # -- running --

    async def run(self, import_id: UUID) -> ImportRunResult:
        """Run one import now. A failure is recorded on the import and raised."""
        lock = self._locks.setdefault(import_id, asyncio.Lock())
        async with lock:
            record = await self._repository.get(import_id)
            if record is None:
                raise MemoryImportNotFoundError(f"memory import {import_id} does not exist")
            try:
                result = await self._run(record)
            except Exception as exc:
                await self._record_failure(record, exc)
                raise
            await self._repository.record_run(record.id, ran_at=self._clock(), error="")
            telemetry = get_observability()
            attributes = {"connector": record.connector}
            telemetry.count(IMPORT_RUNS, attributes=attributes, description="Import runs.")
            telemetry.count(
                IMPORT_SOURCES,
                result.new_sources,
                attributes=attributes,
                description="Sources the gateway created for an import.",
            )
            return result

    async def run_all(self) -> tuple[int, int]:
        """Run every import once: ``(succeeded, failed)``.

        One import failing does not stop the others. Each failure was already recorded on its
        import (``last_error``), logged at error and counted by :meth:`run`, and the import
        runs again on the next call.
        """
        succeeded = 0
        failed = 0
        for record in await self._repository.list_all():
            try:
                await self.run(record.id)
            except MemoryImportNotFoundError:
                # Deleted since the list was read: nothing is left to run.
                continue
            except Exception:  # noqa: BLE001 - recorded, logged and counted in run()
                failed += 1
                continue
            succeeded += 1
        return succeeded, failed

    async def _run(self, record: MemoryImport) -> ImportRunResult:
        issues = await self._read(record)
        new_source_ids: list[str] = []
        accepted: list[UpdatedIssue] = []
        for issue in issues:
            try:
                answer = await self._gateway.write_source(self._source_write(record, issue))
            except Exception:
                await self._save_progress(
                    record, _safe_cursor(record, accepted, issue), new_source_ids
                )
                raise
            accepted.append(issue)
            if answer.new:
                new_source_ids.append(answer.source_id)
        cursor = accepted[-1].updated_at if accepted else record.cursor
        await self._save_progress(record, cursor, new_source_ids)
        compile_run = await self._compile(record, new_source_ids)
        return ImportRunResult(
            read=len(issues),
            new_sources=len(new_source_ids),
            unchanged=len(issues) - len(new_source_ids),
            cursor=cursor,
            compile_run=compile_run,
            new_source_ids=tuple(new_source_ids),
        )

    async def _read(self, record: MemoryImport) -> list[UpdatedIssue]:
        reader = await self._connections.reader(
            record.project_id, record.connection_id, acting_for=record.owner_id
        )
        try:
            issues = await reader.issues_updated_since(record.tracker_project_id, record.cursor)
        finally:
            await reader.close()
        return sorted(issues, key=lambda issue: issue.updated_at)

    def _source_write(self, record: MemoryImport, issue: UpdatedIssue) -> SourceWrite:
        if not issue.url:
            raise MemoryImportError(
                f"ticket {issue.identifier} has no URL, and a source must link to its original; "
                f"check the {record.connector} connection {record.connection_id}"
            )
        return SourceWrite(
            tenant_id=record.tenant_id,
            layer=record.layer,
            connector=record.connector,
            external_id=issue.identifier,
            title=f"{issue.identifier} {issue.title}",
            content=issue_source_content(issue),
            kind=SOURCE_KIND_TICKET,
            origin_url=issue.url,
            fetched_at=self._clock(),
            upstream_updated_at=issue.updated_at,
        )

    async def _save_progress(
        self, record: MemoryImport, cursor: datetime | None, new_source_ids: list[str]
    ) -> None:
        await self._repository.record_progress(
            record.id,
            cursor=cursor,
            add_pending=new_source_ids if record.compile else [],
        )

    async def _compile(self, record: MemoryImport, new_source_ids: list[str]) -> str | None:
        """Start the compile workflow for every source still waiting for one."""
        if not record.compile:
            return None
        waiting = list(dict.fromkeys([*record.pending_source_ids, *new_source_ids]))
        if not waiting:
            return None
        run_id = await self._compiler.launch(
            project_id=record.project_id,
            tenant_id=record.tenant_id,
            owner_id=record.owner_id,
            source_ids=waiting,
        )
        await self._repository.clear_pending(record.id, waiting)
        return run_id

    async def _record_failure(self, record: MemoryImport, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Memory import %s (project %s, %s %s) failed and will run again next interval: %s",
            record.id,
            record.project_id,
            record.connector,
            record.tracker_project_id,
            message,
            exc_info=exc,
        )
        get_observability().count(
            IMPORT_FAILURES,
            attributes={"connector": record.connector},
            description="Import runs that failed.",
        )
        await self._repository.record_run(record.id, ran_at=self._clock(), error=message)


def _safe_cursor(
    record: MemoryImport, accepted: list[UpdatedIssue], failing: UpdatedIssue
) -> datetime | None:
    """The newest change time every ticket up to which was accepted, before ``failing``.

    Tickets that changed at the same moment as the failing one are not covered: the next
    run reads ``updatedAt`` strictly after the cursor, so the cursor stays before them.
    """
    earlier = [issue.updated_at for issue in accepted if issue.updated_at < failing.updated_at]
    return max(earlier) if earlier else record.cursor
