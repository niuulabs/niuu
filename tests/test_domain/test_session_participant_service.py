"""Invite/accept/revoke/list permission cases for durable session participation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.domain.models import Session
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantAlreadyActiveError,
    ParticipantNotFoundError,
    ParticipantRole,
    ParticipantStateError,
    SelfInviteError,
)

OWNER = Principal("owner", "", "acme", ["volundr:developer"])
INVITEE = Principal("invitee", "", "acme", ["volundr:developer"])
OUTSIDER = Principal("outsider", "", "acme", ["volundr:developer"])
CROSS_TENANT_ADMIN = Principal("owner", "", "other-tenant", ["volundr:admin"])


def _user_repository(*, member_of: str = "acme"):
    repo = AsyncMock()
    repo.get.return_value = User(id="invitee", email="invitee@example.test")
    repo.get_memberships.return_value = [TenantMembership(user_id="invitee", tenant_id=member_of)]
    return repo


async def _service(user_repository=None):
    session_repository = InMemorySessionRepository()
    session_service = SessionService(
        session_repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    participant_repository = InMemorySessionParticipantRepository()
    session = Session(name="s", model="m", owner_id="owner", tenant_id="acme")
    await session_repository.create(session)
    service = SessionParticipantService(
        participant_repository, session_service, user_repository or _user_repository()
    )
    return service, session


async def test_non_owner_cannot_invite():
    service, session = await _service()
    with pytest.raises(SessionAccessDeniedError):
        await service.invite(
            session, OUTSIDER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
        )


async def test_cross_tenant_caller_cannot_invite():
    service, session = await _service()
    with pytest.raises(SessionAccessDeniedError):
        await service.invite(
            session,
            CROSS_TENANT_ADMIN,
            user_id="invitee",
            role=ParticipantRole.OBSERVER,
            expires_at=None,
        )


async def test_inviting_a_nonexistent_user_is_rejected():
    user_repository = _user_repository()
    user_repository.get.return_value = None
    service, session = await _service(user_repository)
    with pytest.raises(InviteeNotFoundError):
        await service.invite(
            session, OWNER, user_id="ghost", role=ParticipantRole.OBSERVER, expires_at=None
        )


async def test_inviting_a_user_from_another_tenant_is_rejected():
    service, session = await _service(_user_repository(member_of="other-tenant"))
    with pytest.raises(CrossTenantInviteError):
        await service.invite(
            session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
        )


async def test_owner_can_invite_and_grant_is_invited_until_accepted():
    service, session = await _service()
    participant = await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.APPROVER, expires_at=None
    )
    assert participant.status.value == "invited"
    assert participant.role.value == "approver"
    assert not participant.is_active


async def test_owner_cannot_invite_themselves():
    service, session = await _service()
    with pytest.raises(SelfInviteError):
        await service.invite(
            session, OWNER, user_id="owner", role=ParticipantRole.OBSERVER, expires_at=None
        )


async def test_cannot_invite_the_session_owner():
    service, session = await _service()
    admin = Principal("admin-user", "", "acme", ["volundr:admin"])
    with pytest.raises(OwnerInviteError):
        await service.invite(
            session, admin, user_id="owner", role=ParticipantRole.OBSERVER, expires_at=None
        )


async def test_reinviting_an_active_participant_is_refused():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    with pytest.raises(ParticipantAlreadyActiveError):
        await service.invite(
            session, OWNER, user_id="invitee", role=ParticipantRole.APPROVER, expires_at=None
        )
    # The original role must be unchanged — the attempted re-invite never applied.
    grants = await service.active_grants(session.id)
    assert "invitee" not in grants.approver_ids


async def test_reinviting_a_revoked_participant_is_allowed():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")
    # A revoked (inactive) grant may be re-invited — only ACTIVE is refused.
    participant = await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.APPROVER, expires_at=None
    )
    assert participant.status.value == "invited"
    assert participant.role.value == "approver"


async def test_accept_flow_activates_the_grant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    accepted = await service.accept(session, INVITEE)
    assert accepted.status.value == "active"
    assert accepted.is_active


async def test_accept_with_no_invitation_is_not_found():
    service, session = await _service()
    with pytest.raises(ParticipantNotFoundError):
        await service.accept(session, INVITEE)


async def test_accept_after_expiry_is_rejected_without_activating():
    service, session = await _service()
    past = datetime.now(UTC) - timedelta(seconds=1)
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=past
    )
    with pytest.raises(ParticipantStateError):
        await service.accept(session, INVITEE)
    grants = await service.active_grants(session.id)
    assert "invitee" not in grants.viewer_ids


async def test_owner_can_revoke_any_participant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")
    grants = await service.active_grants(session.id)
    assert "invitee" not in grants.viewer_ids


async def test_participant_can_leave_without_owner_authorization():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, INVITEE, "invitee")
    grants = await service.active_grants(session.id)
    assert "invitee" not in grants.viewer_ids


async def test_non_owner_cannot_revoke_someone_else():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    with pytest.raises(SessionAccessDeniedError):
        await service.revoke(session, OUTSIDER, "invitee")


async def test_revoking_a_nonexistent_grant_is_not_found():
    service, session = await _service()
    with pytest.raises(ParticipantNotFoundError):
        await service.revoke(session, OWNER, "nobody")


async def test_list_participants_visible_to_owner():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    participants = await service.list_participants(session, OWNER)
    assert [p.user_id for p in participants] == ["invitee"]


async def test_list_participants_visible_to_an_active_participant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    participants = await service.list_participants(session, INVITEE)
    assert [p.user_id for p in participants] == ["invitee"]


async def test_list_participants_denied_to_an_uninvited_outsider():
    service, session = await _service()
    with pytest.raises(SessionAccessDeniedError):
        await service.list_participants(session, OUTSIDER)


async def test_check_room_access_grants_attach_to_an_active_participant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.check_room_access(session, INVITEE, "attach")  # does not raise


async def test_check_room_access_denies_resolve_gate_to_a_non_approver_participant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    with pytest.raises(SessionAccessDeniedError):
        await service.check_room_access(session, INVITEE, "resolve_gate")


async def test_check_room_access_denies_attach_after_revoke():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")
    with pytest.raises(SessionAccessDeniedError):
        await service.check_room_access(session, INVITEE, "attach")


# --- list_participant_sessions: widening listing without widening "read" ---


async def test_list_participant_sessions_returns_active_grant_sessions_with_role():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.APPROVER, expires_at=None
    )
    await service.accept(session, INVITEE)

    pairs = await service.list_participant_sessions(INVITEE)

    assert len(pairs) == 1
    returned_session, grant = pairs[0]
    assert returned_session.id == session.id
    assert grant.role == ParticipantRole.APPROVER
    assert grant.user_id == "invitee"


async def test_list_participant_sessions_excludes_invited_not_yet_accepted():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    # Not accepted yet.
    assert await service.list_participant_sessions(INVITEE) == []


async def test_list_participant_sessions_excludes_revoked():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")
    assert await service.list_participant_sessions(INVITEE) == []


async def test_list_participant_sessions_excludes_cross_tenant_grant():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    cross_tenant_invitee = Principal("invitee", "", "other-tenant", ["volundr:developer"])
    assert await service.list_participant_sessions(cross_tenant_invitee) == []


async def test_list_participant_sessions_excludes_archived_by_default():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    from volundr.domain.models import SessionStatus

    archived = session.model_copy(update={"status": SessionStatus.ARCHIVED})
    await service._session_service._repository.update(archived)

    assert await service.list_participant_sessions(INVITEE) == []
    pairs = await service.list_participant_sessions(INVITEE, include_archived=True)
    assert len(pairs) == 1


# --- revoke's immediate-close notifier: best-effort, never masks a real failure ---


async def test_revoke_notifies_the_revocation_hook_with_session_and_user():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    notified = []

    async def notifier(session_id, user_id):
        notified.append((session_id, user_id))

    service.set_revocation_notifier(notifier)
    await service.revoke(session, OWNER, "invitee")

    assert notified == [(session.id, "invitee")]


async def test_revoke_succeeds_even_when_the_notifier_fails():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)

    async def broken_notifier(session_id, user_id):
        raise RuntimeError("proxy unreachable")

    service.set_revocation_notifier(broken_notifier)
    await service.revoke(session, OWNER, "invitee")  # must not raise

    grants = await service.active_grants(session.id)
    assert "invitee" not in grants.viewer_ids  # the revoke itself still took effect


async def test_revoke_without_a_notifier_configured_still_works():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")  # no notifier set; must not raise


# --- effective_room_role: the single source of truth for "what role is X" ---
#
# Shared by volundr.main's in-process room-role resolver (mini-mode's
# session proxy) and the GET .../participants/role endpoint a Kubernetes
# session pod's RemoteAuthorizationAdapter calls remotely.


async def test_effective_room_role_of_the_owner_is_owner():
    service, session = await _service()
    assert await service.effective_room_role(session, OWNER) == "owner"


async def test_effective_room_role_of_an_active_viewer_grant_is_viewer():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    assert await service.effective_room_role(session, INVITEE) == "viewer"


async def test_effective_room_role_of_an_active_approver_grant_is_approver():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.APPROVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    assert await service.effective_room_role(session, INVITEE) == "approver"


async def test_effective_room_role_with_no_grant_is_none():
    """A real "no grant" answer — never a degraded default role."""
    service, session = await _service()
    assert await service.effective_room_role(session, OUTSIDER) is None


async def test_effective_room_role_after_revoke_is_none():
    service, session = await _service()
    await service.invite(
        session, OWNER, user_id="invitee", role=ParticipantRole.OBSERVER, expires_at=None
    )
    await service.accept(session, INVITEE)
    await service.revoke(session, OWNER, "invitee")
    assert await service.effective_room_role(session, INVITEE) is None


async def test_effective_room_role_of_no_principal_raises_like_check_room_access():
    """Matches check_room_access's contract: no principal is a caller error
    (unauthenticated), not a "no grant" answer — callers resolve a principal
    (or return None themselves, as volundr.main's WS resolver does on an
    invalid token) before ever calling this."""
    service, session = await _service()
    with pytest.raises(PermissionError):
        await service.effective_room_role(session, None)
