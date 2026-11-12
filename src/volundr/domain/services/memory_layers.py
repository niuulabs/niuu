"""What the memory gateway asks Forge about the lifetime and ownership of run and session layers.

The gateway keeps a layer per workflow run (``run:<id>``) and per session (``session:<id>``),
and deletes them a retention window after they end. It does not know when a session or a run
ended, or who owns a run; Forge does:

* ``ended`` says, for ids the gateway lists from its namespaces, whether each is still
  ``active``, has ``ended`` (and when), or is ``gone`` (Forge has no such session, or no
  session of that run);
* ``run_owner`` says who a run belongs to, from the sessions that carry
  ``workload_config.workflow_run_id``, so the gateway can check that only the run's owner
  promotes from the run layer.

A session has ended when its status is stopped, failed or archived; ``ended_at`` is its
``updated_at`` then (a later update only delays deletion). A run has ended when none of its
sessions is active; ``ended_at`` is the latest of theirs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from identity.models import TenantRole
from volundr.domain.models import Principal, Session, SessionStatus
from volundr.domain.services.session import SessionService
from volundr.domain.workflow_session import WORKFLOW_RUN_ID_KEY

#: A session in one of these states is not running and will not run until restarted.
ENDED_STATUSES = frozenset({SessionStatus.STOPPED, SessionStatus.FAILED, SessionStatus.ARCHIVED})


class LayerState(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    GONE = "gone"


@dataclass(frozen=True)
class LayerEnd:
    state: LayerState
    ended_at: datetime | None = None


@dataclass(frozen=True)
class EndedLayers:
    sessions: dict[str, LayerEnd]
    runs: dict[str, LayerEnd]


@dataclass(frozen=True)
class RunOwner:
    run_id: str
    owner_id: str
    tenant_id: str
    session_ids: tuple[UUID, ...]


def _run_id(session: Session) -> str:
    value = session.workload_config.get(WORKFLOW_RUN_ID_KEY)
    return str(value) if value else ""


def _session_end(session: Session | None) -> LayerEnd:
    if session is None:
        return LayerEnd(LayerState.GONE)
    if session.status not in ENDED_STATUSES:
        return LayerEnd(LayerState.ACTIVE)
    return LayerEnd(LayerState.ENDED, session.updated_at)


def _run_end(sessions: list[Session]) -> LayerEnd:
    if not sessions:
        return LayerEnd(LayerState.GONE)
    if any(session.status not in ENDED_STATUSES for session in sessions):
        return LayerEnd(LayerState.ACTIVE)
    return LayerEnd(LayerState.ENDED, max(session.updated_at for session in sessions))


class MemoryLayerService:
    """Answers the gateway's questions about run and session layers."""

    def __init__(self, sessions: SessionService) -> None:
        self._sessions = sessions

    async def ended(self, session_ids: list[UUID], run_ids: list[str]) -> EndedLayers:
        """The state of each session and run; ids Forge has no record of are ``gone``."""
        found = await self._sessions.get_many_sessions(session_ids)
        by_run: dict[str, list[Session]] = defaultdict(list)
        for session in await self._sessions.list_workflow_run_sessions(run_ids):
            by_run[_run_id(session)].append(session)
        return EndedLayers(
            sessions={
                str(session_id): _session_end(found.get(session_id)) for session_id in session_ids
            },
            runs={run_id: _run_end(by_run.get(run_id, [])) for run_id in run_ids},
        )

    async def run_owner(self, run_id: str, principal: Principal) -> RunOwner | None:
        """Who owns the run, or ``None`` when the caller may not know.

        The run is the sessions of the caller's tenant that carry it. A tenant admin may ask
        about any of them; anyone else only when they own all of them. A run with no session
        the caller can see and a run that is someone else's answer the same, so a run id
        cannot be probed.
        """
        sessions = [
            session
            for session in await self._sessions.list_workflow_run_sessions([run_id])
            if session.tenant_id == principal.tenant_id
        ]
        if not sessions:
            return None
        if TenantRole.ADMIN not in principal.roles and any(
            session.owner_id != principal.user_id for session in sessions
        ):
            return None
        return RunOwner(
            run_id=run_id,
            owner_id=sessions[0].owner_id or "",
            tenant_id=principal.tenant_id,
            session_ids=tuple(session.id for session in sessions),
        )
