"""Shared in-memory stubs reused across Tyr test modules."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from tyr.domain.flock_flow import FlockFlowConfig
from tyr.domain.models import (
    ConfidenceEvent,
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from tyr.ports.flock_flow import FlockFlowProvider
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import (
    ActivityEvent,
    PRStatus,
    SpawnRequest,
    VolundrPort,
    VolundrSession,
)


class InMemorySagaRepository(SagaRepository):
    """In-memory saga repository for tests."""

    def __init__(self) -> None:
        self.sagas: dict[UUID, Saga] = {}
        self.phases: dict[UUID, Phase] = {}
        self.raids: dict[UUID, Raid] = {}

    async def save_saga(self, saga: Saga, *, conn: Any = None) -> None:
        self.sagas[saga.id] = saga

    async def save_phase(self, phase: Phase, *, conn: Any = None) -> None:
        self.phases[phase.id] = phase

    async def save_raid(self, raid: Raid, *, conn: Any = None) -> None:
        self.raids[raid.id] = raid

    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        if owner_id is None:
            return list(self.sagas.values())
        return [s for s in self.sagas.values() if s.owner_id == owner_id]

    async def get_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> Saga | None:
        return self.sagas.get(saga_id)

    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        return next((s for s in self.sagas.values() if s.slug == slug), None)

    async def delete_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> bool:
        return self.sagas.pop(saga_id, None) is not None

    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        saga = self.sagas.get(saga_id)
        if saga:
            self.sagas[saga_id] = Saga(
                id=saga.id,
                tracker_id=saga.tracker_id,
                tracker_type=saga.tracker_type,
                slug=saga.slug,
                name=saga.name,
                repos=saga.repos,
                feature_branch=saga.feature_branch,
                base_branch=saga.base_branch,
                status=status,
                confidence=saga.confidence,
                created_at=saga.created_at,
                owner_id=saga.owner_id,
            )

    async def count_by_status(self) -> dict[str, int]:
        return {}

    async def get_phase(self, phase_id: UUID) -> Phase | None:
        return self.phases.get(phase_id)

    async def get_raid(self, raid_id: UUID) -> Raid | None:
        return self.raids.get(raid_id)

    async def get_raids_by_phase(self, phase_id: UUID) -> list[Raid]:
        return [r for r in self.raids.values() if r.phase_id == phase_id]

    async def get_phases_by_saga(self, saga_id: UUID) -> list[Phase]:
        return sorted(
            [p for p in self.phases.values() if p.saga_id == saga_id],
            key=lambda p: p.number,
        )

    async def update_raid_outcome(
        self,
        raid_id: UUID,
        outcome: dict[str, Any],
        event_type: str,
        status: RaidStatus,
    ) -> None:
        raid = self.raids.get(raid_id)
        if raid is None:
            return
        self.raids[raid_id] = Raid(
            id=raid.id,
            phase_id=raid.phase_id,
            tracker_id=raid.tracker_id,
            name=raid.name,
            description=raid.description,
            acceptance_criteria=raid.acceptance_criteria,
            declared_files=raid.declared_files,
            estimate_hours=raid.estimate_hours,
            status=status,
            confidence=raid.confidence,
            session_id=raid.session_id,
            branch=raid.branch,
            chronicle_summary=raid.chronicle_summary,
            pr_url=raid.pr_url,
            pr_id=raid.pr_id,
            retry_count=raid.retry_count,
            created_at=raid.created_at,
            updated_at=datetime.now(UTC),
            identifier=raid.identifier,
            url=raid.url,
            reviewer_session_id=raid.reviewer_session_id,
            review_round=raid.review_round,
            structured_outcome=outcome,
            outcome_event_type=event_type,
        )


class StubVolundrPort(VolundrPort):
    """Stub Volundr port that records spawn requests."""

    def __init__(self, session_id: str = "sess-001") -> None:
        self._session_id = session_id
        self.spawned: list[SpawnRequest] = []
        self.integration_ids: list[str] = []

    async def spawn_session(
        self, request: SpawnRequest, *, auth_token: str | None = None
    ) -> VolundrSession:
        self.spawned.append(request)
        return VolundrSession(
            id=self._session_id,
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
        )

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return None

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return []

    async def get_pr_status(self, session_id: str) -> PRStatus:
        return PRStatus(exists=False, merged=False, url=None, ci_passed=False)

    async def get_chronicle_summary(self, session_id: str) -> str:
        return ""

    async def send_message(
        self, session_id: str, message: str, *, auth_token: str | None = None
    ) -> None:
        pass

    async def stop_session(self, session_id: str, *, auth_token: str | None = None) -> None:
        pass

    async def list_integration_ids(self, *, auth_token: str | None = None) -> list[str]:
        return list(self.integration_ids)

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        return ""

    async def get_conversation(self, session_id: str) -> dict:
        return {}

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        return
        yield  # type: ignore[misc]


class StubFlockFlowProvider(FlockFlowProvider):
    """In-memory flock flow provider for tests."""

    def __init__(self, flows: dict[str, FlockFlowConfig] | None = None) -> None:
        self._flows: dict[str, FlockFlowConfig] = flows or {}

    def get(self, name: str) -> FlockFlowConfig | None:
        return self._flows.get(name)

    def list(self) -> list[FlockFlowConfig]:
        return list(self._flows.values())

    def save(self, flow: FlockFlowConfig) -> None:
        self._flows[flow.name] = flow

    def delete(self, name: str) -> bool:
        return self._flows.pop(name, None) is not None


class StubVolundrFactory:
    """Factory that always returns the same stub adapter."""

    def __init__(self, volundr: VolundrPort | None = None) -> None:
        self._volundr = volundr or StubVolundrPort()

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        if self._volundr is None:
            return []
        return [self._volundr]

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._volundr


# ---------------------------------------------------------------------------
# Tracker stubs (used by ravn outcome tests and review engine tests)
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)
_DEFAULT_OWNER = "test-owner"
_DEFAULT_TRACKER_ID = "raid-tracker-001"
_DEFAULT_SESSION = "sess-ravn-001"


class StubTracker(TrackerPort):
    """Minimal in-memory tracker for Tyr unit tests."""

    def __init__(self, raid: Raid | None = None) -> None:
        self._raid = raid
        self._raids_by_session: dict[str, Raid] = {}
        if raid is not None:
            self._raids_by_id = {raid.tracker_id: raid}
            if raid.session_id:
                self._raids_by_session[raid.session_id] = raid
        else:
            self._raids_by_id: dict[str, Raid] = {}
        self.confidence_events: dict[str, list[ConfidenceEvent]] = {}
        self.phase: Phase | None = None
        self.saga: Saga | None = None
        self._phases: list[Phase] = []
        self._all_merged: bool = False
        self.closed_raids: list[str] = []

    @property
    def raids(self) -> dict[str, Raid]:
        """Public alias for _raids_by_id used in tests."""
        return self._raids_by_id

    # -- CRUD: create entities --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return saga.tracker_id

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return phase.tracker_id

    async def create_raid(self, raid: Raid, *, project_id: str = "", milestone_id: str = "") -> str:
        self._raids_by_id[raid.tracker_id] = raid
        if raid.session_id:
            self._raids_by_session[raid.session_id] = raid
        return raid.tracker_id

    async def update_raid_state(self, raid_id: str, state: RaidStatus) -> None:
        pass

    async def close_raid(self, raid_id: str) -> None:
        self.closed_raids.append(raid_id)

    # -- Read --

    async def get_saga(self, saga_id: str) -> Saga:
        if self.saga is None:
            raise ValueError("No saga")
        return self.saga

    async def get_phase(self, tracker_id: str) -> Phase:
        if self.phase is None:
            raise ValueError("No phase")
        return self.phase

    async def get_raid(self, tracker_id: str) -> Raid:
        raid = self._raids_by_id.get(tracker_id)
        if raid is None:
            raise ValueError(f"Raid not found: {tracker_id}")
        return raid

    async def list_pending_raids(self, phase_id: str) -> list[Raid]:
        return []

    async def list_projects(self) -> list[TrackerProject]:
        return []

    async def get_project(self, project_id: str) -> TrackerProject:
        raise NotImplementedError

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return []

    async def list_issues(
        self, project_id: str, milestone_id: str | None = None
    ) -> list[TrackerIssue]:
        return []

    # -- Raid progress --

    async def update_raid_progress(
        self,
        tracker_id: str,
        *,
        status: RaidStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
        reviewer_session_id: str | None = None,
        review_round: int | None = None,
    ) -> Raid:
        raid = self._raids_by_id.get(tracker_id)
        if raid is None:
            raise ValueError(f"Raid not found: {tracker_id}")
        events = self.confidence_events.get(tracker_id, [])
        new_confidence = events[-1].score_after if events else raid.confidence
        updated = Raid(
            id=raid.id,
            phase_id=raid.phase_id,
            tracker_id=raid.tracker_id,
            name=raid.name,
            description=raid.description,
            acceptance_criteria=raid.acceptance_criteria,
            declared_files=raid.declared_files,
            estimate_hours=raid.estimate_hours,
            status=status if status is not None else raid.status,
            confidence=confidence if confidence is not None else new_confidence,
            session_id=session_id if session_id is not None else raid.session_id,
            branch=raid.branch,
            chronicle_summary=raid.chronicle_summary,
            pr_url=pr_url if pr_url is not None else raid.pr_url,
            pr_id=pr_id if pr_id is not None else raid.pr_id,
            retry_count=retry_count if retry_count is not None else raid.retry_count,
            created_at=raid.created_at,
            updated_at=datetime.now(UTC),
            reviewer_session_id=(
                reviewer_session_id if reviewer_session_id is not None else raid.reviewer_session_id
            ),
            review_round=review_round if review_round is not None else raid.review_round,
        )
        self._raids_by_id[tracker_id] = updated
        if updated.session_id:
            self._raids_by_session[updated.session_id] = updated
        return updated

    async def get_raid_progress_for_saga(self, saga_tracker_id: str) -> list[Raid]:
        return list(self._raids_by_id.values())

    async def get_raid_by_session(self, session_id: str) -> Raid | None:
        return self._raids_by_session.get(session_id)

    async def list_raids_by_status(self, status: RaidStatus) -> list[Raid]:
        return [r for r in self._raids_by_id.values() if r.status == status]

    async def get_raid_by_id(self, raid_id: UUID) -> Raid | None:
        return next((r for r in self._raids_by_id.values() if r.id == raid_id), None)

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        self.confidence_events.setdefault(tracker_id, []).append(event)

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        return self.confidence_events.get(tracker_id, [])

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
        return self._all_merged

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return self._phases

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_raid(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_raid(self, tracker_id: str) -> Phase | None:
        return self.phase

    async def get_owner_for_raid(self, tracker_id: str) -> str | None:
        return _DEFAULT_OWNER

    async def save_session_message(self, message: SessionMessage) -> None:
        pass

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        return []

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        return "doc-1"


class StubTrackerFactory:
    def __init__(self, tracker: StubTracker) -> None:
        self._tracker = tracker

    async def for_owner(self, owner_id: str) -> list[StubTracker]:
        return [self._tracker]


class StubGit:
    """Minimal in-memory git stub for Tyr unit tests."""

    def __init__(self) -> None:
        self.pr_statuses: dict[str, PRStatus] = {}
        self.changed_files: dict[str, list[str]] = {}

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        pass

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        pass

    async def delete_branch(self, repo: str, branch: str) -> None:
        pass

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-1"

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        pr = self.pr_statuses.get(pr_id)
        if pr is None:
            raise RuntimeError(f"No PR: {pr_id}")
        return pr

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return self.changed_files.get(pr_id, [])


def make_raid(
    *,
    status: RaidStatus = RaidStatus.REVIEW,
    confidence: float = 0.5,
    session_id: str | None = _DEFAULT_SESSION,
    retry_count: int = 0,
    tracker_id: str = _DEFAULT_TRACKER_ID,
) -> Raid:
    """Build a minimal Raid for use in tests."""
    return Raid(
        id=uuid4(),
        phase_id=uuid4(),
        tracker_id=tracker_id,
        name="test-raid",
        description="A test raid",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=1.0,
        status=status,
        confidence=confidence,
        session_id=session_id,
        branch=None,
        chronicle_summary=None,
        pr_url=None,
        pr_id=None,
        retry_count=retry_count,
        created_at=_NOW,
        updated_at=_NOW,
    )
