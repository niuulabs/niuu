"""Who holds which role in a session's room, from every source that can grant one.

A session's room roles come from its per-session grants (``session_participants``) and,
when it is attached to a project, from the project's members (``room_role_for_project_role``).
:func:`~volundr.domain.session_participants.compute_room_grants` merges them; this class
fetches both inputs. Session authorization (Cedar's ``read_room`` / ``resolve_gate`` sets)
and notification fan-out (who is told that something needs an approver) both read the
roles through here, so the person who is told is exactly the person who may answer.
"""

from __future__ import annotations

from volundr.domain.models import Session
from volundr.domain.ports import SessionParticipantRepository
from volundr.domain.project_ports import ProjectMemberRepository
from volundr.domain.projects import ProjectMember
from volundr.domain.session_participants import RoomGrants, compute_room_grants


class RoomGrantSource:
    """Reads a session's participant grants and its project's members."""

    def __init__(
        self,
        participants: SessionParticipantRepository,
        project_members: ProjectMemberRepository | None,
    ) -> None:
        self._participants = participants
        # ``None`` is the configured decision that this host runs without projects
        # (``projects.enabled: false``): such a host has no session attached to a project.
        self._project_members = project_members

    @property
    def has_projects(self) -> bool:
        return self._project_members is not None

    async def project_members(self, session: Session) -> list[ProjectMember]:
        """Members of the session's project that belong to the session's tenant."""
        if self._project_members is None or session.coordination is None:
            return []
        members = await self._project_members.list_for_project(session.coordination.project_id)
        return [m for m in members if m.tenant_id == session.tenant_id]

    async def for_session(self, session: Session) -> RoomGrants:
        """The viewer and approver sets from ACTIVE, unexpired grants and project members."""
        return compute_room_grants(
            await self._participants.list_active_for_session(session.id),
            await self.project_members(session),
        )
