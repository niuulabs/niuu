"""Durable per-session collaboration grants (shared agent rooms).

Several participants can share one agent room in the browser through DURABLE
grants: the owner (or an admin) invites a same-tenant user with a role, the
invitee accepts, and a grant can expire. Authorization never trusts the raw
``status`` column alone — an ``ACTIVE`` grant whose ``expires_at`` has passed
is inactive (see ``SessionParticipant.is_active``), and Cedar policy only ever
sees the *computed* active/unexpired sets built by ``compute_room_grants``.

All four roles read the room and speak in it by default; ``approver`` additionally
resolves workflow gates and answers tool-permission prompts. ``teacher`` and
``debugger`` exist as distinct invitation intents but carry the same baseline
grant as ``observer`` in this version — there is no third permission tier for
them yet.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class ParticipantRole(StrEnum):
    OBSERVER = "observer"
    TEACHER = "teacher"
    DEBUGGER = "debugger"
    APPROVER = "approver"


class ParticipantStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    REVOKED = "revoked"


class SessionParticipant(BaseModel):
    """One durable collaboration grant for a user on a session."""

    session_id: UUID
    user_id: str
    tenant_id: str
    role: ParticipantRole
    status: ParticipantStatus
    invited_by: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """True status is not enough: a past ``expires_at`` makes a grant inactive.

        Expiry is judged here, once, so every caller (Cedar attribute
        computation, the accept/list endpoints) agrees on what "active" means
        instead of re-deriving it from the raw row.
        """
        if self.status != ParticipantStatus.ACTIVE:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > datetime.now(UTC)


class ParticipantNotFoundError(LookupError):
    """No participant grant exists for this (session, user) pair."""


class ParticipantStateError(ValueError):
    """The requested transition is invalid for the grant's current state."""


class InviteeNotFoundError(LookupError):
    """The user being invited does not exist.

    The REST layer maps this to the SAME generic response as
    CrossTenantInviteError — distinguishing "doesn't exist" from "wrong
    tenant" over HTTP would let an inviter enumerate which user_ids exist.
    """


class CrossTenantInviteError(ValueError):
    """The invitee is not a member of the session's tenant.

    See InviteeNotFoundError: mapped to the same generic HTTP response.
    """


class SelfInviteError(ValueError):
    """A caller cannot invite themselves; they already have whatever access
    got them past the admit check in the first place."""


class OwnerInviteError(ValueError):
    """The session owner cannot be invited as a participant; they already
    have full, unconditional access and are never subject to expiry/revoke."""


class ParticipantAlreadyActiveError(ValueError):
    """Re-inviting an ACTIVE grant is refused, not silently applied.

    Changing an active participant's role is a distinct decision from
    inviting a new one — collapsing them risks a caller demoting (or
    unintentionally re-elevating) someone already in the room without
    meaning to. There is no separate "change role" endpoint yet: revoke
    then re-invite is the explicit path today.
    """


class RoomGrants(BaseModel):
    """The Cedar-visible Set attributes computed from ACTIVE, unexpired grants.

    Two sets, not three: the owner decided against a separate "speaker" tier
    (every active participant may read the room and speak in it), so there is
    no room_speakers attribute or "speak" Cedar action — keep it that way
    rather than carrying a set that always equals viewer_ids. Per-message-type
    authorization inside the room (who may send which control) is enforced by
    Skuld's broker against the room role the session proxy stamps, not Cedar.
    """

    viewer_ids: frozenset[str] = frozenset()
    approver_ids: frozenset[str] = frozenset()


def compute_room_grants(participants: Iterable[SessionParticipant]) -> RoomGrants:
    """Reduce a session's participant rows to the two Cedar room-grant sets.

    Every active participant, regardless of role, may read the room and
    speak in it; only ``approver`` grants also land in ``approver_ids``.
    """
    active = [p for p in participants if p.is_active]
    viewer_ids = frozenset(p.user_id for p in active)
    approver_ids = frozenset(p.user_id for p in active if p.role == ParticipantRole.APPROVER)
    return RoomGrants(viewer_ids=viewer_ids, approver_ids=approver_ids)
