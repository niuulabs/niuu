"""Domain service for durable per-session collaboration grants (shared agent rooms).

Owns the invite/accept/revoke/list workflow and the room-scoped authorization
check (``read_room``/``attach``/``resolve_gate``) that Cedar decides from the
ACTIVE, unexpired grants this service computes. This is deliberately separate
from ``SessionService._check_access``: that method's actions
(read/update/delete/start/stop/...) never see room grants, and these new
room actions never go through it — the two authorization surfaces do not mix.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from volundr.domain.models import Principal, Session, SessionStatus
from volundr.domain.ports import SessionParticipantRepository, UserRepository
from volundr.domain.project_ports import ProjectMemberRepository
from volundr.domain.projects import ProjectMember
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantAlreadyActiveError,
    ParticipantCandidate,
    ParticipantNotFoundError,
    ParticipantRole,
    ParticipantStateError,
    ParticipantStatus,
    RoomGrants,
    SelfInviteError,
    SessionNotInProjectError,
    SessionParticipant,
    room_role_for_project_role,
)

from .room_grants import RoomGrantSource
from .session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)


class InvitationNotices(Protocol):
    """Tells an invitee of the invitation, and closes the notice once it is answered.

    Implemented by the notification feed; absent on a host that runs without one, which
    is the operator's decision (the invitee still finds the invitation in their list).
    """

    async def invitation_sent(
        self, session: Session, participant: SessionParticipant, *, invited_by_name: str
    ) -> None:
        """Tell *participant* they were invited into *session* by *invited_by_name*."""

    async def invitation_settled(
        self, session: Session, participant: SessionParticipant, *, resolution: str
    ) -> None:
        """Close the invitee's notice once the invitation is answered; *resolution* says how."""


class SessionParticipantService:
    """Invite, accept, revoke, and list a session's durable collaboration grants."""

    def __init__(
        self,
        repository: SessionParticipantRepository,
        session_service: SessionService,
        user_repository: UserRepository,
        project_members: ProjectMemberRepository | None = None,
    ) -> None:
        self._repository = repository
        self._session_service = session_service
        self._user_repository = user_repository
        # The members of a session's project hold a room role on it. ``None`` is the
        # configured decision that this host runs without projects (``projects.enabled:
        # false``): such a host has no session attached to a project either.
        self._project_members = project_members
        self._room_grants = RoomGrantSource(repository, project_members)
        # Optional async hook: notified with (session_id, user_id) right after
        # a revoke commits, so the composition root can close an
        # ALREADY-OPEN proxied socket immediately instead of relying solely
        # on the session proxy's interval revalidation (see
        # niuu.session_proxy._revalidate_loop, which is still the fallback
        # for anything this hook misses or isn't wired for). Set by the
        # composition root via set_revocation_notifier(); never required —
        # revoke() must succeed even where no live-socket layer is wired.
        self._revocation_notifier: Callable[[UUID, str], Awaitable[None]] | None = None
        self._invitation_notices: InvitationNotices | None = None

    def set_invitation_notices(self, notices: InvitationNotices) -> None:
        """Tell invitees of invitations through ``notices`` (the notification feed)."""
        self._invitation_notices = notices

    def set_revocation_notifier(self, notifier: Callable[[UUID, str], Awaitable[None]]) -> None:
        """Inject the immediate-close hook invoked after a successful revoke."""
        self._revocation_notifier = notifier

    async def invite(
        self,
        session: Session,
        principal: Principal | None,
        *,
        user_id: str,
        role: ParticipantRole,
        expires_at: datetime | None,
    ) -> SessionParticipant:
        """Invite *user_id* to *session*'s room.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The caller is not the session's owner or an admin.
            SelfInviteError: The caller tried to invite themselves.
            OwnerInviteError: The invitee is the session's owner.
            InviteeNotFoundError: No such user exists.
            CrossTenantInviteError: The invitee is not a member of the session's tenant.
            ParticipantAlreadyActiveError: The invitee already has an active grant;
                use revoke + re-invite to change their role explicitly.
        """
        # "admit" is the Cedar action for room admission decisions (inviting or
        # revoking a participant), distinct from "update"'s general session
        # mutation and never granted to a room participant, only session-owner
        # and session-admin.
        await self.check_room_access(session, principal, "admit")
        if principal is not None and principal.user_id == user_id:
            raise SelfInviteError("Cannot invite yourself")
        if user_id == session.owner_id:
            raise OwnerInviteError("The session owner already has full access")
        invitee = await self._user_repository.get(user_id)
        if invitee is None:
            raise InviteeNotFoundError(f"No such user: {user_id}")
        memberships = await self._user_repository.get_memberships(user_id)
        if not any(m.tenant_id == session.tenant_id for m in memberships):
            raise CrossTenantInviteError(
                f"User {user_id} is not a member of tenant {session.tenant_id}"
            )
        existing = await self._repository.get(session.id, user_id)
        if existing is not None and existing.is_active:
            raise ParticipantAlreadyActiveError(
                f"User {user_id} already has an active grant; revoke it first to change role"
            )
        invited_by = principal.user_id if principal is not None else "unauthenticated"
        if existing is not None and existing.status == ParticipantStatus.INVITED:
            # Inviting again (another role, a new expiry) replaces the open invitation, so
            # the invitee has one open notice, not a stale one beside the new one.
            await self._settle_invitation(session, existing, "Replaced by a new invitation.")
        participant = await self._repository.invite(
            session.id, user_id, session.tenant_id or "", role, invited_by, expires_at
        )
        if self._invitation_notices is not None:
            # Not swallowed: the grant exists, so a failure here is the caller's to see and
            # retry (inviting again is idempotent), not an invitee nobody told.
            inviter = await self._user_repository.get(invited_by)
            await self._invitation_notices.invitation_sent(
                session,
                participant,
                invited_by_name=(inviter.display_name or inviter.email or invited_by)
                if inviter is not None
                else invited_by,
            )
        return participant

    async def candidates(
        self, session: Session, principal: Principal | None, query: str
    ) -> list[ParticipantCandidate]:
        """Active members of the session's org who could be invited into its chat.

        For the people who may invite (the owner and admins): the org's members who
        are not the owner and do not hold an invited or active grant already.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The caller is not the session's owner or an admin.
        """
        await self.check_room_access(session, principal, "admit")
        return await self._repository.candidates(
            session.id, session.tenant_id or "", session.owner_id or "", query.strip()
        )

    async def find_people(
        self, principal: Principal | None, query: str
    ) -> list[ParticipantCandidate]:
        """Active members of the caller's org, other than the caller, matching *query*.

        For choosing people to invite before a session exists (a team being set up), so
        it needs no session and no grant: anyone who belongs to the org may search it,
        since anyone who belongs to it may start a session of their own.

        Raises:
            PermissionError: No principal was given.
        """
        if principal is None:
            raise PermissionError("An authenticated principal is required")
        return await self._repository.tenant_people(
            principal.tenant_id, principal.user_id, query.strip()
        )

    async def accept(self, session: Session, principal: Principal) -> SessionParticipant:
        """The invited user accepts, moving their grant from invited to active.

        Raises:
            ParticipantNotFoundError: No grant is invited for this principal.
            ParticipantStateError: The grant exists but has already expired.
        """
        existing = await self._repository.get(session.id, principal.user_id)
        if existing is None or existing.status != ParticipantStatus.INVITED:
            raise ParticipantNotFoundError(
                f"No invited grant for user {principal.user_id} on session {session.id}"
            )
        if existing.expires_at is not None and existing.expires_at <= datetime.now(UTC):
            raise ParticipantStateError("This invitation has expired")
        accepted = await self._repository.accept(session.id, principal.user_id)
        if accepted is None:
            raise ParticipantNotFoundError(
                f"No invited grant for user {principal.user_id} on session {session.id}"
            )
        await self._settle_invitation(session, existing, "Accepted.")
        return accepted

    async def revoke(self, session: Session, principal: Principal | None, user_id: str) -> None:
        """Revoke a grant: the owner/admin may revoke anyone, a participant may leave.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: A non-owner/admin tried to revoke someone else.
            ParticipantNotFoundError: No grant exists for that user.
        """
        self_leaving = principal is not None and principal.user_id == user_id
        if not self_leaving:
            await self.check_room_access(session, principal, "admit")
        held = await self._repository.get(session.id, user_id)
        revoked = await self._repository.revoke(session.id, user_id)
        if revoked is None:
            raise ParticipantNotFoundError(f"No grant for user {user_id} on session {session.id}")
        if held is not None and held.status == ParticipantStatus.INVITED:
            await self._settle_invitation(
                session, held, "Declined." if self_leaving else "Withdrawn by the owner."
            )
        if self._revocation_notifier is not None:
            # Best-effort, immediate: the grant IS revoked in the database
            # regardless of this outcome, and the session proxy's interval
            # revalidation is the mechanism of record that always eventually
            # closes a live socket — so a notifier failure must never turn a
            # successful revoke into a reported failure. It is logged, not
            # silently dropped: an immediate-close outage should be visible.
            try:
                await self._revocation_notifier(session.id, user_id)
            except Exception:
                logger.warning(
                    "Immediate-close revocation notifier failed for session=%s user=%s; "
                    "the session proxy's interval revalidation will still close it",
                    session.id,
                    user_id,
                    exc_info=True,
                )

    async def _settle_invitation(
        self, session: Session, invitation: SessionParticipant, resolution: str
    ) -> None:
        if self._invitation_notices is None:
            return
        await self._invitation_notices.invitation_settled(
            session, invitation, resolution=resolution
        )

    async def display_names(self, user_ids: set[str]) -> dict[str, str]:
        """The name each of these people goes by (display name, else email), where known.

        For the owner of a session shared with someone: the room summary names who shared
        it. A person the directory does not know has no entry, never a made-up name.
        """
        names: dict[str, str] = {}
        for user_id in sorted(user_ids):
            user = await self._user_repository.get(user_id)
            name = (user.display_name or user.email) if user is not None else ""
            if name:
                names[user_id] = name
        return names

    async def list_grants(self, session: Session) -> list[SessionParticipant]:
        """The grants that count as people of *session*: active, or invited and still open.

        A revoked grant and an expired one are not in the session. This is a lookup, not an
        authorization: the caller has already decided who may see the answer.
        """
        return [
            grant
            for grant in await self._repository.list_for_session(session.id)
            if grant.is_active or grant.is_pending
        ]

    async def people_counts(self, session_ids: list[UUID]) -> dict[UUID, int]:
        """How many people each session has: its owner and its active, unexpired grants.

        One query for the whole list, so listing sessions never asks once per session.
        Every id is in the answer; a session nobody was invited to counts its owner alone.
        """
        if not session_ids:
            return {}
        granted = await self._repository.count_active_for_sessions(session_ids, datetime.now(UTC))
        return {session_id: 1 + granted.get(session_id, 0) for session_id in session_ids}

    async def participation(
        self, session: Session, principal: Principal
    ) -> tuple[str | None, SessionParticipant | None]:
        """The caller's room role on *session* and their own pending or active grant.

        ``(None, None)`` means the caller has no relation to the session at all. An
        invitee who has not accepted has a grant but no room role yet.
        """
        grant = await self._repository.get(session.id, principal.user_id)
        if grant is not None and grant.status == ParticipantStatus.REVOKED:
            grant = None
        return await self.effective_room_role(session, principal), grant

    async def list_participants(
        self, session: Session, principal: Principal | None
    ) -> list[SessionParticipant]:
        """List every grant (any status) for the session's room.

        Visible to the session's owner/admin, and to any of the room's own
        active participants (so a participant can see who else is in the
        room) — never to an ordinary tenant volundr:viewer, unlike "read":
        this uses "update" (owner/admin-only, see session-owner/session-admin)
        precisely because Cedar's session-viewer policy grants "read" to any
        tenant viewer regardless of ownership, which would otherwise let a
        completely unrelated tenant viewer list any room's participants.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: Neither an owner/admin nor an active participant.
        """
        try:
            await self._session_service._check_access(session, principal, "update")
        except SessionAccessDeniedError:
            if not await self._is_active_participant(session.id, principal):
                raise
        return await self._repository.list_for_session(session.id)

    async def _is_active_participant(self, session_id: UUID, principal: Principal | None) -> bool:
        if principal is None:
            return False
        existing = await self.get_own_grant(session_id, principal.user_id)
        return existing is not None and existing.is_active

    async def get_own_grant(self, session_id: UUID, user_id: str) -> SessionParticipant | None:
        """Return *user_id*'s own grant for a session, regardless of status."""
        return await self._repository.get(session_id, user_id)

    async def list_participant_sessions(
        self, principal: Principal, *, include_archived: bool = False
    ) -> list[tuple[Session, SessionParticipant]]:
        """Sessions *principal* actively participates in, regardless of ownership.

        Returns each session paired with *principal*'s own grant, so the
        caller can render a room summary (including the caller's role)
        without an extra per-session query.

        This widens session *listing* (``ForgeService.list_participant_only_
        sessions``) so an invited participant finds the room in their list
        instead of hitting a 404 navigating to it directly. It is authorized
        through Cedar's ``read_room`` action (batched via
        ``SessionService.filter_authorized``), never ``read`` — it never
        widens what a participant may DO with a session's deeper content
        (diffs, files, chronicles stay owner/admin — see
        ``check_room_access``), only that the room appears in their list.

        Batched: one query for every active grant this user holds, one
        query to fetch the sessions, one Cedar evaluation for all of them.
        """
        held = [
            *(
                g
                for g in await self._repository.list_active_for_user(principal.user_id)
                if g.is_active
            ),
            # An invitation not yet answered: the invitee finds the session to accept or
            # decline it. It gives no room access (the room sets above see active grants
            # only) and the summary built from it carries no room content.
            *(
                g
                for g in await self._repository.list_invited_for_user(principal.user_id)
                if g.is_pending
            ),
        ]
        grants = [g for g in held if g.tenant_id == principal.tenant_id]
        sessions_by_id = (
            await self._session_service.get_many_sessions([g.session_id for g in grants])
            if grants
            else {}
        )
        by_session: dict[UUID, tuple[Session, SessionParticipant]] = {
            g.session_id: (sessions_by_id[g.session_id], g)
            for g in grants
            if g.session_id in sessions_by_id
        }
        for session, project_grant in await self._project_sessions(principal):
            held = by_session.get(session.id)
            # A project member's access is real; a pending invitation is only an offer.
            if (
                held is None
                or held[1].status == ParticipantStatus.INVITED
                or _room_rank(project_grant.role) > _room_rank(held[1].role)
            ):
                by_session[session.id] = (session, project_grant)
        candidates: list[tuple[Session, SessionParticipant]] = []
        resources = []
        for session, grant in by_session.values():
            if not include_archived and session.status == SessionStatus.ARCHIVED:
                continue
            candidates.append((session, grant))
            # A singleton set is enough for Cedar's .contains(principal.user_id)
            # check — this grant alone already establishes the principal's
            # own membership, so there is no need to recompute the session's
            # full room_viewers/room_approvers sets here.
            resources.append(
                SessionService.attributed_resource(
                    str(session.id),
                    owner_id=session.owner_id,
                    tenant_id=session.tenant_id,
                    room_viewers=[principal.user_id],
                    room_approvers=[principal.user_id]
                    if grant.role == ParticipantRole.APPROVER
                    else [],
                )
            )
        if not candidates:
            return []
        allowed_ids = {
            r.id
            for r in await self._session_service.filter_authorized(
                principal, "read_room", resources
            )
        }
        return [(s, g) for s, g in candidates if str(s.id) in allowed_ids]

    async def _project_sessions(
        self, principal: Principal
    ) -> list[tuple[Session, SessionParticipant]]:
        """Sessions of the projects *principal* is a member of, each with the room
        grant their project role gives (shaped as a participant grant so the room
        summary renders it like an invited one)."""
        if self._project_members is None:
            return []
        memberships = {
            m.project_id: m
            for m in await self._project_members.list_for_user(
                principal.user_id, principal.tenant_id
            )
        }
        if not memberships:
            return []
        sessions = await self._session_service.list_project_sessions(
            list(memberships), principal.tenant_id
        )
        return [
            (session, _project_grant(session, memberships[session.coordination.project_id]))
            for session in sessions
            if session.coordination is not None and session.coordination.project_id in memberships
        ]

    # --- Room-scoped authorization (read_room / attach / speak / resolve_gate) --

    async def active_grants(self, session: Session) -> RoomGrants:
        """Compute the Cedar-visible room grant sets for *session*.

        Built from its ACTIVE, unexpired participant rows and from the members of the
        project it is attached to; the more senior of the two applies to a user in both.
        """
        return await self._room_grants.for_session(session)

    async def list_people(
        self, session: Session, principal: Principal | None
    ) -> list[ProjectMember]:
        """The members of the project *session* is attached to, for anyone in its room.

        Authorized by ``read_room`` like the room itself: the session's owner, its
        participants, project members and tenant admins. Each member carries the role,
        expertise and skills the project holds for them.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The policy denies ``read_room``.
            SessionNotInProjectError: The session has no project (or this host runs
                without projects), so there are no members to list.
        """
        await self.check_room_access(session, principal, "read_room")
        if session.coordination is None:
            raise SessionNotInProjectError(
                f"Session {session.id} is not attached to a project, so it has no project "
                "members. Attach it to a project (coordination.project_id) to list people."
            )
        if self._project_members is None:
            raise SessionNotInProjectError(
                f"Session {session.id} names a project, but this Forge runs without projects "
                "(projects.enabled is false), so it cannot list the project's members."
            )
        return await self._room_grants.project_members(session)

    async def check_room_access(
        self, session: Session, principal: Principal | None, action: str
    ) -> None:
        """Authorize a room-scoped action via Cedar, using computed active grants.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The policy denies *action*.
        """
        grants = await self.active_grants(session)
        resource = SessionService.attributed_resource(
            str(session.id),
            owner_id=session.owner_id,
            tenant_id=session.tenant_id,
            room_viewers=grants.viewer_ids,
            room_approvers=grants.approver_ids,
        )
        if not await self._session_service.authorizes(principal, action, resource):
            user_id = principal.user_id if principal is not None else "unauthenticated"
            raise SessionAccessDeniedError(session.id, user_id)

    async def effective_room_role(
        self, session: Session, principal: Principal | None
    ) -> str | None:
        """Return *principal*'s most senior room role for *session*, or None.

        Derived from the SAME Cedar decisions ``check_room_access`` and the
        REST API use (``admit``/``resolve_gate``/``read_room``) — never a
        hand-written owner_id/admin-role comparison, which silently stops
        matching the moment authority is granted any way other than literal
        ownership or a tenant-admin role. Ranked most-to-least senior: a
        principal who can ``admit`` is owner, one who can ``resolve_gate`` is
        approver, one who can only ``read_room`` is viewer, and one who can
        do none of these has no room role at all (``None`` — a real "no
        grant" answer, not a degraded default).

        This is the single source of truth for room-role resolution shared
        by the mini-mode session proxy's in-process resolver
        (``volundr.main``) and the ``POST .../participants/role`` endpoint
        that a Kubernetes-backed session pod's ``RemoteAuthorizationAdapter``
        (``skuld.room_role_remote``) calls over HTTP.
        """
        grants = await self.active_grants(session)
        resource = SessionService.attributed_resource(
            str(session.id),
            owner_id=session.owner_id,
            tenant_id=session.tenant_id,
            room_viewers=grants.viewer_ids,
            room_approvers=grants.approver_ids,
        )
        if await self._session_service.authorizes(principal, "admit", resource):
            return "owner"
        if await self._session_service.authorizes(principal, "resolve_gate", resource):
            return "approver"
        if await self._session_service.authorizes(principal, "read_room", resource):
            return "viewer"
        return None


_ROOM_ROLE_RANK = {ParticipantRole.VIEWER: 0, ParticipantRole.APPROVER: 1}


def _room_rank(role: ParticipantRole) -> int:
    return _ROOM_ROLE_RANK[role]


def _project_grant(session: Session, member: ProjectMember) -> SessionParticipant:
    return SessionParticipant(
        session_id=session.id,
        user_id=member.user_id,
        tenant_id=member.tenant_id,
        role=room_role_for_project_role(member.role),
        status=ParticipantStatus.ACTIVE,
        invited_by=member.added_by,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )
