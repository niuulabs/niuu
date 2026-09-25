"""Cedar policy coverage for the room-scoped actions (read_room/attach/resolve_gate/admit).

There is no separate "speak" Cedar action: the owner decided every active
participant may speak, so "read_room"/"attach" already cover it — see
RoomGrants' docstring in volundr.domain.session_participants. Per-message-type
authorization inside the room (who may send which broker control) is Skuld's
job (tests/test_skuld/test_room_role_message_gating.py), not Cedar's.
"""

import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import Resource
from niuu.domain.models import Principal

OWNER = Principal("owner", "", "acme", ["volundr:developer"])
ADMIN = Principal("admin", "", "acme", ["volundr:admin"])
VIEWER_PARTICIPANT = Principal("viewer-p", "", "acme", ["volundr:viewer"])
DEVELOPER_PARTICIPANT = Principal("developer-p", "", "acme", ["volundr:developer"])
APPROVER_PARTICIPANT = Principal("approver-p", "", "acme", ["volundr:developer"])
ROLELESS_PARTICIPANT = Principal("roleless-p", "", "acme", [])
NON_PARTICIPANT = Principal("stranger", "", "acme", ["volundr:developer"])
CROSS_TENANT_PARTICIPANT = Principal("viewer-p", "", "other-tenant", ["volundr:developer"])


def _resource() -> Resource:
    return Resource(
        "session",
        "s1",
        {
            "owner_id": "owner",
            "tenant_id": "acme",
            "room_viewers": [
                "viewer-p",
                "developer-p",
                "approver-p",
                "roleless-p",
            ],
            "room_approvers": ["approver-p"],
        },
    )


@pytest.fixture
def adapter():
    return CedarAuthorizationAdapter()


@pytest.mark.parametrize("action", ["read_room", "attach", "resolve_gate", "admit"])
async def test_owner_gets_every_new_room_action(adapter, action):
    assert await adapter.is_allowed(OWNER, action, _resource())


@pytest.mark.parametrize("action", ["read_room", "attach", "resolve_gate", "admit"])
async def test_admin_gets_every_new_room_action(adapter, action):
    assert await adapter.is_allowed(ADMIN, action, _resource())


@pytest.mark.parametrize("action", ["read_room", "attach"])
async def test_any_active_participant_gets_baseline_room_actions(adapter, action):
    for principal in (VIEWER_PARTICIPANT, DEVELOPER_PARTICIPANT, APPROVER_PARTICIPANT):
        assert await adapter.is_allowed(principal, action, _resource())


async def test_only_approver_role_gets_resolve_gate(adapter):
    assert await adapter.is_allowed(APPROVER_PARTICIPANT, "resolve_gate", _resource())
    assert not await adapter.is_allowed(VIEWER_PARTICIPANT, "resolve_gate", _resource())
    assert not await adapter.is_allowed(DEVELOPER_PARTICIPANT, "resolve_gate", _resource())


async def test_participant_never_gets_admit(adapter):
    for principal in (VIEWER_PARTICIPANT, DEVELOPER_PARTICIPANT, APPROVER_PARTICIPANT):
        assert not await adapter.is_allowed(principal, "admit", _resource())


async def test_participation_without_a_volundr_role_grants_nothing(adapter):
    # In room_viewers, but no volundr:* tenant role at all.
    assert not await adapter.is_allowed(ROLELESS_PARTICIPANT, "read_room", _resource())
    assert not await adapter.is_allowed(ROLELESS_PARTICIPANT, "attach", _resource())


@pytest.mark.parametrize("action", ["read_room", "attach", "resolve_gate", "admit"])
async def test_a_non_participant_gets_nothing(adapter, action):
    assert not await adapter.is_allowed(NON_PARTICIPANT, action, _resource())


async def test_cross_tenant_isolation_beats_room_membership(adapter):
    # Same user_id as a legitimate viewer, but authenticated into another
    # tenant: the tenant-isolation forbid must still win.
    assert not await adapter.is_allowed(CROSS_TENANT_PARTICIPANT, "read_room", _resource())


async def test_room_grants_never_leak_into_the_plain_read_action(adapter):
    """read_room covers room state; it must never widen the ordinary "read" action.

    DEVELOPER_PARTICIPANT (role volundr:developer, not owner) is deliberately
    not "volundr:viewer" here: that role already grants plain "read" on any
    tenant session regardless of room membership (session-viewer, pre-existing
    and unrelated to participants), which would confound this assertion.
    """
    assert not await adapter.is_allowed(DEVELOPER_PARTICIPANT, "read", _resource())
