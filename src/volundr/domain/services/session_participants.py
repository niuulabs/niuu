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
from uuid import UUID

from volundr.domain.models import Principal, Session, SessionStatus
from volundr.domain.ports import SessionParticipantRepository, UserRepository
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantAlreadyActiveError,
    ParticipantNotFoundError,
    ParticipantRole,
    ParticipantStateError,
    ParticipantStatus,
    RoomGrants,
    SelfInviteError,
    SessionParticipant,
    compute_room_grants,
)

from .session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)


class SessionParticipantService:
    """Invite, accept, revoke, and list a session's durable collaboration grants."""

    def __init__(
        self,
        repository: SessionParticipantRepository,
        session_service: SessionService,
        user_repository: UserRepository,
    ) -> None:
        self._repository = repository
        self._session_service = session_service
        self._user_repository = user_repository
        # Optional async hook: notified with (session_id, user_id) right after
        # a revoke commits, so the composition root can close an
        # ALREADY-OPEN proxied socket immediately instead of relying solely
        # on the session proxy's interval revalidation (see
        # niuu.session_proxy._revalidate_loop, which is still the fallback
        # for anything this hook misses or isn't wired for). Set by the
        # composition root via set_revocation_notifier(); never required —
        # revoke() must succeed even where no live-socket layer is wired.
        self._revocation_notifier: Callable[[UUID, str], Awaitable[None]] | None = None

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
        return await self._repository.invite(
            session.id, user_id, session.tenant_id or "", role, invited_by, expires_at
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
        revoked = await self._repository.revoke(session.id, user_id)
        if revoked is None:
            raise ParticipantNotFoundError(f"No grant for user {user_id} on session {session.id}")
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
        grants = [
            g
            for g in await self._repository.list_active_for_user(principal.user_id)
            if g.is_active and g.tenant_id == principal.tenant_id
        ]
        if not grants:
            return []
        sessions_by_id = await self._session_service.get_many_sessions(
            [g.session_id for g in grants]
        )
        candidates: list[tuple[Session, SessionParticipant]] = []
        resources = []
        for grant in grants:
            session = sessions_by_id.get(grant.session_id)
            if session is None:
                continue
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
        allowed_ids = {
            r.id
            for r in await self._session_service.filter_authorized(
                principal, "read_room", resources
            )
        }
        return [(s, g) for s, g in candidates if str(s.id) in allowed_ids]

    # --- Room-scoped authorization (read_room / attach / speak / resolve_gate) --

    async def active_grants(self, session_id: UUID) -> RoomGrants:
        """Compute the Cedar-visible room grant sets from ACTIVE, unexpired rows."""
        return compute_room_grants(await self._repository.list_active_for_session(session_id))

    async def check_room_access(
        self, session: Session, principal: Principal | None, action: str
    ) -> None:
        """Authorize a room-scoped action via Cedar, using computed active grants.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The policy denies *action*.
        """
        grants = await self.active_grants(session.id)
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
        grants = await self.active_grants(session.id)
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
