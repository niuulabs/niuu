"""Record a session's launch stages as its runtime backend reports them.

The recorder owns ``sessions.startup``. A launch begins with Forge's own stage
(running the contributor pipeline); from then on the pod manager reports the
backend's stages and the recorder merges them while the session is starting.
When the session leaves starting, the recorder closes the launch: running ends
every open stage, a failure marks the stage it happened in with the reason.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from niuu.observability import get_observability
from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import EventBroadcaster, PodManager, SessionRepository
from volundr.domain.session_startup import SessionStartup, StartupStage

logger = logging.getLogger(__name__)

BOOTING_STATUSES = frozenset({SessionStatus.STARTING, SessionStatus.PROVISIONING})

LAUNCH_STAGE = "launch"
RUNTIME_STAGE = "runtime"


def _now() -> datetime:
    return datetime.now(UTC)


class SessionStartupRecorder:
    """Persist and broadcast a session's launch stages."""

    def __init__(
        self,
        repository: SessionRepository,
        pod_manager: PodManager,
        broadcaster: EventBroadcaster | None,
        poll_seconds: float,
    ):
        if poll_seconds <= 0:
            raise ValueError("provisioning.startup_poll_seconds must be positive")
        self._repository = repository
        self._pod_manager = pod_manager
        self._broadcaster = broadcaster
        self._poll_seconds = poll_seconds
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._watches: dict[UUID, asyncio.Task] = {}

    async def begin(self, session: Session) -> Session:
        """Start a new launch record: Forge is preparing the launch."""
        now = _now()
        startup = SessionStartup.begin(
            now, StartupStage(key=LAUNCH_STAGE, label="Preparing launch", started_at=now)
        )
        return await self._store(session.id, lambda _: startup, replace=True)

    async def handing_off(self, session_id: UUID) -> None:
        """The contributor pipeline is done and the runtime backend takes over.

        A backend that reports its own stages continues the record; for one that
        does not, Forge can only say the runtime is starting.
        """
        now = _now()
        if self._pod_manager.reports_startup_stages:
            await self._store(session_id, lambda startup: startup.handed_off(now))
            return
        stage = StartupStage(key=RUNTIME_STAGE, label="Starting the runtime", started_at=now)
        await self._store(session_id, lambda startup: startup.handed_off(now).with_stage(stage))

    def watch(self, session_id: UUID) -> None:
        """Follow the backend's stages until the session leaves starting."""
        existing = self._watches.get(session_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._watch(session_id), name=f"startup-{session_id}")
        self._watches[session_id] = task
        task.add_done_callback(lambda _: self._forget(session_id))

    def _forget(self, session_id: UUID) -> None:
        self._watches.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def _watch(self, session_id: UUID) -> None:
        while True:
            session = await self._repository.get(session_id)
            if session is None or session.startup is None:
                return
            if session.status not in BOOTING_STATUSES:
                await self._close(session)
                return
            await self._observe(session)
            await asyncio.sleep(self._poll_seconds)

    async def _observe(self, session: Session) -> None:
        if session.startup is None or session.startup.handed_off_at is None:
            # Still in Forge's own pipeline: whatever the backend holds now is
            # left over from an earlier launch.
            return
        try:
            stages = await self._pod_manager.startup_stages(session)
        except Exception as exc:
            # The launch goes on; its progress is unknown, and the record says so.
            detail = str(exc).strip() or type(exc).__name__
            logger.error(
                "Could not read the startup stages of session %s: %s",
                session.id,
                detail,
                exc_info=True,
            )
            get_observability().count("volundr.session.startup_progress_errors")
            await self._store(session.id, lambda startup: startup.with_progress_error(detail))
            return
        if stages is None:
            return
        await self._store(session.id, lambda startup: startup.with_stages(stages))

    async def _close(self, session: Session) -> None:
        now = _now()
        if session.status is SessionStatus.RUNNING:
            await self._store(session.id, lambda startup: startup.finished(now))
            return
        if session.status is SessionStatus.FAILED:
            reason = session.error or "The session failed while starting"
            await self._store(session.id, lambda startup: startup.failed(now, reason))
            return
        if session.startup is not None and session.startup.current is not None:
            reason = f"The session was {session.status.value} before it was ready"
            await self._store(session.id, lambda startup: startup.failed(now, reason))

    async def _store(
        self,
        session_id: UUID,
        change: Callable[[SessionStartup], SessionStartup],
        *,
        replace: bool = False,
    ) -> Session | None:
        """Apply ``change`` to the stored record; ``replace`` starts one where none is."""
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            session = await self._repository.get(session_id)
            if session is None:
                return None
            current = session.startup
            if current is None and not replace:
                return session
            updated = change(current)
            if updated == current:
                return session
            stored = await self._repository.update_startup(session_id, updated)
        if stored is not None and self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(stored)
        return stored
