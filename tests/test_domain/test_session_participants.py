"""Expiry judged once, in one place, and the room-grant sets it drives."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from volundr.domain.session_participants import (
    ParticipantRole,
    ParticipantStatus,
    SessionParticipant,
    compute_room_grants,
)


def _participant(
    *,
    user_id: str = "user-1",
    role: ParticipantRole = ParticipantRole.OBSERVER,
    status: ParticipantStatus = ParticipantStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> SessionParticipant:
    now = datetime.now(UTC)
    return SessionParticipant(
        session_id=uuid4(),
        user_id=user_id,
        tenant_id="acme",
        role=role,
        status=status,
        invited_by="owner",
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )


def test_active_status_with_no_expiry_is_active():
    assert _participant(status=ParticipantStatus.ACTIVE).is_active


def test_active_status_with_future_expiry_is_active():
    future = datetime.now(UTC) + timedelta(hours=1)
    assert _participant(status=ParticipantStatus.ACTIVE, expires_at=future).is_active


def test_active_status_with_past_expiry_is_inactive():
    past = datetime.now(UTC) - timedelta(seconds=1)
    assert not _participant(status=ParticipantStatus.ACTIVE, expires_at=past).is_active


def test_invited_status_is_never_active_regardless_of_expiry():
    assert not _participant(status=ParticipantStatus.INVITED).is_active


def test_revoked_status_is_never_active():
    assert not _participant(status=ParticipantStatus.REVOKED).is_active


def test_compute_room_grants_puts_every_active_role_in_viewer():
    participants = [
        _participant(user_id="observer-1", role=ParticipantRole.OBSERVER),
        _participant(user_id="teacher-1", role=ParticipantRole.TEACHER),
        _participant(user_id="debugger-1", role=ParticipantRole.DEBUGGER),
        _participant(user_id="approver-1", role=ParticipantRole.APPROVER),
    ]
    grants = compute_room_grants(participants)
    all_ids = {"observer-1", "teacher-1", "debugger-1", "approver-1"}
    assert grants.viewer_ids == all_ids
    assert grants.approver_ids == {"approver-1"}


def test_compute_room_grants_excludes_expired_and_revoked_and_invited():
    past = datetime.now(UTC) - timedelta(seconds=1)
    participants = [
        _participant(user_id="expired", role=ParticipantRole.APPROVER, expires_at=past),
        _participant(user_id="revoked", status=ParticipantStatus.REVOKED),
        _participant(user_id="invited", status=ParticipantStatus.INVITED),
        _participant(user_id="active", role=ParticipantRole.APPROVER),
    ]
    grants = compute_room_grants(participants)
    assert grants.viewer_ids == {"active"}
    assert grants.approver_ids == {"active"}


def test_compute_room_grants_of_no_participants_is_all_empty_sets():
    grants = compute_room_grants([])
    assert grants.viewer_ids == frozenset()
    assert grants.approver_ids == frozenset()
