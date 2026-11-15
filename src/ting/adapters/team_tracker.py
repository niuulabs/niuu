"""A team (project-owned) tracker connection that records every write it makes.

A team connection acts as the project's bot, not as the person it works for, so the
project must be able to see what was done with it and for whom. :class:`TeamTrackerRecorder`
stands in for the real adapter for every :class:`TrackerPort` call and, after each write
that succeeded, records one :class:`IntegrationUsage` through the shared usage log. Reads
are not recorded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from niuu.cap_telemetry import record_degraded
from niuu.domain.models import IntegrationUsage
from niuu.ports.integrations import IntegrationUsageLog
from ting.domain.memory_imports import UpdatedIssue
from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.ports.memory_imports import IssueUpdateReader
from ting.ports.tracker import TrackerPort

#: ``IntegrationUsage.actor`` of every use Ting makes.
USAGE_ACTOR = "ting"
#: ``IntegrationUsage.action`` of a memory import's read of a team connection.
IMPORT_ACTION = "import"
#: ``record_degraded`` path: the write happened and its usage record could not be saved.
DEGRADED_PATH = "ting.team_tracker_usage"


class TeamTrackerRecorder(TrackerPort):
    """Delegates to ``inner`` and records each successful write against the project."""

    def __init__(
        self,
        inner: TrackerPort,
        *,
        usage_log: IntegrationUsageLog,
        connection_id: str,
        project_id: str,
        acting_for: str,
    ) -> None:
        self._inner = inner
        self._usage_log = usage_log
        self._usage_connection_id = connection_id
        self._usage_project_id = project_id
        self._acting_for = acting_for

    def __getattr__(self, name: str) -> Any:
        # Adapter-specific surface the port does not declare (only reached when the
        # recorder itself has no such attribute).
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    # -- connection binding: the routing code reads these off the adapter --

    def bind_connection(self, *, connection_id: str, provider: str, name: str) -> None:
        self._inner.bind_connection(connection_id=connection_id, provider=provider, name=name)

    @property
    def connection_id(self) -> str:
        return self._inner.connection_id

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def connection_name(self) -> str:
        return self._inner.connection_name

    # -- recording --

    async def _record(self, action: str, subject: str) -> None:
        """Record a write that already happened; a failure is reported, not raised."""
        usage = IntegrationUsage(
            connection_id=self._usage_connection_id,
            project_id=self._usage_project_id,
            action=action,
            acting_for=self._acting_for,
            actor=USAGE_ACTOR,
            subject=subject,
        )
        try:
            await self._usage_log.record(usage)
        except Exception as exc:
            record_degraded(
                DEGRADED_PATH,
                f"{action} on team connection {self._usage_connection_id} (project "
                f"{self._usage_project_id}) succeeded but its usage record could not be "
                f"saved: {exc}; check the integration_usage table and database connection",
            )

    # -- writes --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        tracker_id = await self._inner.create_saga(saga, description=description)
        await self._record("create_saga", tracker_id)
        return tracker_id

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        tracker_id = await self._inner.create_phase(phase, project_id=project_id)
        await self._record("create_phase", tracker_id)
        return tracker_id

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        tracker_id = await self._inner.create_run(
            run, project_id=project_id, milestone_id=milestone_id
        )
        await self._record("create_run", tracker_id)
        return tracker_id

    async def attach_document(self, project_id: str, title: str, content: str) -> str:
        document_id = await self._inner.attach_document(project_id, title, content)
        await self._record("attach_document", document_id or project_id)
        return document_id

    async def add_comment(self, issue_id: str, body: str) -> None:
        await self._inner.add_comment(issue_id, body)
        await self._record("add_comment", issue_id)

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        document_id = await self._inner.attach_issue_document(issue_id, title, content)
        await self._record("attach_issue_document", issue_id)
        return document_id

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        await self._inner.update_run_state(run_id, state)
        await self._record("update_run_state", run_id)

    async def close_run(self, run_id: str) -> None:
        await self._inner.close_run(run_id)
        await self._record("close_run", run_id)

    async def update_run_progress(self, tracker_id: str, **fields: Any) -> Run:
        run = await self._inner.update_run_progress(tracker_id, **fields)
        await self._record("update_run_progress", tracker_id)
        return run

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        phase = await self._inner.update_phase_status(phase_tracker_id, status)
        await self._record("update_phase_status", phase_tracker_id)
        return phase

    async def save_session_message(self, message: SessionMessage) -> None:
        await self._inner.save_session_message(message)
        await self._record("save_session_message", str(message.run_id))

    # -- reads: delegated, never recorded --

    async def get_saga(self, saga_id: str) -> Saga:
        return await self._inner.get_saga(saga_id)

    async def get_phase(self, tracker_id: str) -> Phase:
        return await self._inner.get_phase(tracker_id)

    async def get_run(self, tracker_id: str) -> Run:
        return await self._inner.get_run(tracker_id)

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return await self._inner.list_pending_runs(phase_id)

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        return await self._inner.get_blocked_identifiers(project_id)

    async def list_projects(self) -> list[TrackerProject]:
        return await self._inner.list_projects()

    async def get_project(self, project_id: str) -> TrackerProject:
        return await self._inner.get_project(project_id)

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return await self._inner.list_milestones(project_id)

    async def list_issues(
        self, project_id: str, milestone_id: str | None = None
    ) -> list[TrackerIssue]:
        return await self._inner.list_issues(project_id, milestone_id)

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return await self._inner.get_run_progress_for_saga(saga_tracker_id)

    async def get_authorized_run_progress_for_saga(
        self, saga_tracker_id: str, *, owner_id: str, tenant_id: str
    ) -> list[Run]:
        return await self._inner.get_authorized_run_progress_for_saga(
            saga_tracker_id, owner_id=owner_id, tenant_id=tenant_id
        )

    async def has_unscoped_run_progress_for_saga(
        self, saga_tracker_id: str, *, owner_id: str, tenant_id: str
    ) -> bool:
        return await self._inner.has_unscoped_run_progress_for_saga(
            saga_tracker_id, owner_id=owner_id, tenant_id=tenant_id
        )

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return await self._inner.get_run_by_session(session_id)

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return await self._inner.list_runs_by_status(status)

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return await self._inner.get_run_by_id(run_id)

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return await self._inner.all_runs_merged(phase_tracker_id)

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return await self._inner.list_phases_for_saga(saga_tracker_id)

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return await self._inner.get_saga_for_run(tracker_id)

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return await self._inner.get_phase_for_run(tracker_id)

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return await self._inner.get_owner_for_run(tracker_id)

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        return await self._inner.get_session_messages(tracker_id)


class TeamIssueReader(IssueUpdateReader):
    """A memory import's read of a team connection, recorded before it is made.

    Unlike the tracker writes above, an import's reads are the whole use of the connection,
    so each is recorded as ``IntegrationUsage(action="import", actor="ting")`` first: a read
    whose use cannot be recorded does not happen, and the failure is raised.
    """

    def __init__(
        self,
        inner: IssueUpdateReader,
        *,
        usage_log: IntegrationUsageLog,
        connection_id: str,
        project_id: str,
        acting_for: str,
    ) -> None:
        self._inner = inner
        self._usage_log = usage_log
        self._connection_id = connection_id
        self._project_id = project_id
        self._acting_for = acting_for

    async def issues_updated_since(
        self, tracker_project_id: str, since: datetime | None
    ) -> list[UpdatedIssue]:
        await self._usage_log.record(
            IntegrationUsage(
                connection_id=self._connection_id,
                project_id=self._project_id,
                action=IMPORT_ACTION,
                acting_for=self._acting_for,
                actor=USAGE_ACTOR,
                subject=tracker_project_id,
            )
        )
        return await self._inner.issues_updated_since(tracker_project_id, since)

    async def close(self) -> None:
        await self._inner.close()
