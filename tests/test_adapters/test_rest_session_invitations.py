"""Inviting someone into one chat: the invitee is told, finds it, and answers it.

Real Cedar and a real notification service over in-memory stores: the owner invites a
member of the org, the invitee's feed and session list carry the invitation, accepting or
declining settles it, and the owner can search the org for people to invite.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from tests.support.notifications import InMemoryNotificationStore
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.adapters.inbound.rest_session_participants import create_session_participants_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitSource, Session
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService

PREFIX = "/api/v1/forge"
PEOPLE = {
    "owner": ("Olivia Owner", "olivia@example.test"),
    "invitee": ("Ivan Invitee", "ivan@example.test"),
    "zed": ("Zed Zimmer", "zed@example.test"),
}


def _headers(user_id: str, roles: str = "volundr:developer") -> dict:
    return {"x-auth-user-id": user_id, "x-auth-tenant": "acme", "x-auth-roles": roles}


class Forge:
    def __init__(self) -> None:
        sessions = InMemorySessionRepository()
        self.session_service = SessionService(
            sessions, MockPodManager(), authorization=CedarAuthorizationAdapter()
        )
        users = AsyncMock()
        users.get.side_effect = lambda user_id: (
            User(id=user_id, email=PEOPLE[user_id][1], display_name=PEOPLE[user_id][0])
            if user_id in PEOPLE
            else None
        )
        users.get_memberships.return_value = [TenantMembership(user_id="x", tenant_id="acme")]
        self.repository = InMemorySessionParticipantRepository()
        self.repository.directory = [
            (user_id, name, email, "acme") for user_id, (name, email) in PEOPLE.items()
        ]
        self.participants = SessionParticipantService(self.repository, self.session_service, users)
        store = InMemoryNotificationStore()
        self.notifications = NotificationService(
            store.feed,
            store.rule_repo,
            store.outbox,
            image_repository=store.images,
            image_scheduler=store.image_scheduler,
            reply_ready_enabled=True,
            reply_title_chars=80,
            reply_body_chars=100,
            sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        )
        self.participants.set_invitation_notices(self.notifications)
        app = FastAPI()
        app.state.identity = AllowAllIdentityAdapter(user_repository=users)

        class _Settings:
            local_mounts = LocalMountsConfig()

        app.state.settings = _Settings()
        app.state.admin_settings = {}
        app.include_router(
            create_router(self.session_service, session_participant_service=self.participants)
        )
        app.include_router(
            create_session_participants_router(self.participants, self.session_service)
        )
        app.include_router(
            create_notifications_router(
                self.notifications,
                self.session_service,
                prefix=PREFIX,
                default_page_size=20,
                max_page_size=50,
                image_cache_max_age_seconds=3600,
            )
        )
        self.client = TestClient(app)
        self.session = asyncio.run(
            sessions.create(
                Session(
                    name="Fix the login",
                    model="m",
                    owner_id="owner",
                    tenant_id="acme",
                    chat_endpoint="ws://10.1.2.3:8080/session",
                    source=GitSource(repo="https://github.com/acme/repo", branch="main"),
                )
            )
        )
        self.path = f"{PREFIX}/sessions/{self.session.id}"

    def invite(self, user_id="invitee", role="viewer", as_user="owner"):
        return self.client.post(
            f"{self.path}/participants",
            json={"user_id": user_id, "role": role},
            headers=_headers(as_user),
        )

    def feed(self, user_id: str) -> list[dict]:
        response = self.client.get(f"{PREFIX}/notifications", headers=_headers(user_id))
        assert response.status_code == 200
        return response.json()["items"]

    def listed(self, user_id: str) -> dict | None:
        response = self.client.get(f"{PREFIX}/sessions", headers=_headers(user_id))
        assert response.status_code == 200
        return {item["id"]: item for item in response.json()}.get(str(self.session.id))


@pytest.fixture
def forge() -> Forge:
    return Forge()


class TestTellingTheInvitee:
    def test_the_invitee_gets_one_row_that_reads_as_an_invitation(self, forge):
        assert forge.invite(role="approver").status_code == 201

        [row] = forge.feed("invitee")
        assert row["title"] == "Olivia Owner invited you to Fix the login"
        assert row["body"] == "You can join this chat as approver."
        assert row["session_id"] == str(forge.session.id)
        assert row["resolved"] is False
        assert forge.feed("owner") == []
        assert forge.feed("zed") == []

    def test_inviting_again_while_invited_leaves_one_open_invitation(self, forge):
        forge.invite(role="viewer")
        forge.invite(role="approver")

        rows = forge.feed("invitee")
        open_rows = [row for row in rows if not row["resolved"]]
        assert len(open_rows) == 1
        assert open_rows[0]["body"] == "You can join this chat as approver."
        assert [row["resolution"] for row in rows if row["resolved"]] == [
            "Replaced by a new invitation."
        ]

    def test_accepting_settles_it(self, forge):
        forge.invite()
        forge.client.post(f"{forge.path}/participants/accept", headers=_headers("invitee"))

        [row] = forge.feed("invitee")
        assert row["resolved"] is True
        assert row["resolution"] == "Accepted."

    def test_declining_settles_it(self, forge):
        forge.invite()
        response = forge.client.delete(
            f"{forge.path}/participants/invitee", headers=_headers("invitee")
        )

        assert response.status_code == 204
        [row] = forge.feed("invitee")
        assert row["resolved"] is True and row["resolution"] == "Declined."

    def test_the_owner_withdrawing_it_settles_it(self, forge):
        forge.invite()
        forge.client.delete(f"{forge.path}/participants/invitee", headers=_headers("owner"))

        [row] = forge.feed("invitee")
        assert row["resolution"] == "Withdrawn by the owner."

    def test_inviting_someone_again_after_they_declined_tells_them_again(self, forge):
        forge.invite()
        forge.client.delete(f"{forge.path}/participants/invitee", headers=_headers("invitee"))
        forge.invite()

        rows = forge.feed("invitee")
        assert len(rows) == 2
        assert sorted(row["resolved"] for row in rows) == [False, True]

    def test_a_refused_invitation_tells_nobody(self, forge):
        response = forge.invite(as_user="zed")

        assert response.status_code == 403
        assert forge.feed("invitee") == []


class TestFindingTheInvitation:
    def test_an_invited_session_is_in_the_invitees_list_as_an_invitation(self, forge):
        forge.invite(role="approver")

        summary = forge.listed("invitee")

        assert summary["kind"] == "room_summary"
        assert summary["name"] == "Fix the login"
        assert summary["owner_id"] == "owner"
        assert summary["room"]["role"] == "approver"
        assert summary["room"]["participant_status"] == "invited"

    def test_an_invitation_carries_no_room_content(self, forge):
        forge.invite()

        summary = forge.listed("invitee")

        assert summary["chat_endpoint"] is None
        assert summary["coordination"] is None
        assert summary["activity_state"] is None
        for owner_only in ("source", "code_endpoint", "pod_name", "error"):
            assert owner_only not in summary

    def test_it_gives_no_access_to_the_session(self, forge):
        forge.invite()

        assert forge.client.get(forge.path, headers=_headers("invitee")).status_code == 403
        assert (
            forge.client.get(f"{forge.path}/room", headers=_headers("invitee")).status_code == 403
        )

    def test_accepting_turns_it_into_a_joined_session_with_its_chat(self, forge):
        forge.invite()
        forge.client.post(f"{forge.path}/participants/accept", headers=_headers("invitee"))

        summary = forge.listed("invitee")

        assert summary["room"]["participant_status"] == "active"
        assert summary["chat_endpoint"] == "ws://10.1.2.3:8080/session"

    def test_declining_removes_it_from_the_list(self, forge):
        forge.invite()
        forge.client.delete(f"{forge.path}/participants/invitee", headers=_headers("invitee"))

        assert forge.listed("invitee") is None

    def test_someone_not_invited_does_not_see_it(self, forge):
        forge.invite()

        assert forge.listed("zed") is None

    def test_my_participation_tells_an_invitee_their_grant_before_they_can_read_anything(
        self, forge
    ):
        forge.invite(role="approver")

        response = forge.client.get(f"{forge.path}/participants/me", headers=_headers("invitee"))

        assert response.status_code == 200
        body = response.json()
        assert body["room_role"] is None
        assert body["grant"]["status"] == "invited" and body["grant"]["role"] == "approver"

    def test_my_participation_tells_the_owner_they_own_it(self, forge):
        response = forge.client.get(f"{forge.path}/participants/me", headers=_headers("owner"))

        assert response.status_code == 200
        assert response.json()["room_role"] == "owner"
        assert response.json()["grant"] is None

    def test_my_participation_is_a_404_for_a_stranger_and_for_someone_who_left(self, forge):
        assert (
            forge.client.get(f"{forge.path}/participants/me", headers=_headers("zed")).status_code
            == 404
        )
        forge.invite()
        forge.client.delete(f"{forge.path}/participants/invitee", headers=_headers("invitee"))
        assert (
            forge.client.get(
                f"{forge.path}/participants/me", headers=_headers("invitee")
            ).status_code
            == 404
        )


class TestCandidates:
    def _candidates(self, forge, user="owner", **params):
        return forge.client.get(
            f"{forge.path}/participant-candidates", headers=_headers(user), params=params
        )

    def test_the_owner_finds_the_orgs_people_but_not_themselves(self, forge):
        response = self._candidates(forge)

        assert response.status_code == 200
        assert response.json() == [
            {"user_id": "invitee", "display_name": "Ivan Invitee", "email": "ivan@example.test"},
            {"user_id": "zed", "display_name": "Zed Zimmer", "email": "zed@example.test"},
        ]

    def test_it_narrows_by_what_is_typed(self, forge):
        assert [c["user_id"] for c in self._candidates(forge, q="zim").json()] == ["zed"]

    def test_it_leaves_out_people_already_invited(self, forge):
        forge.invite()

        assert [c["user_id"] for c in self._candidates(forge).json()] == ["zed"]

    def test_only_the_owner_or_an_admin_may_ask(self, forge):
        assert self._candidates(forge, user="zed").status_code == 403
        forge.invite()
        forge.client.post(f"{forge.path}/participants/accept", headers=_headers("invitee"))
        assert self._candidates(forge, user="invitee").status_code == 403
        assert (
            self._candidates(
                forge,
                user="zed",
            ).status_code
            == 403
        )

    def test_an_admin_may_ask(self, forge):
        response = forge.client.get(
            f"{forge.path}/participant-candidates",
            headers=_headers("zed", roles="volundr:admin"),
        )
        assert response.status_code == 200

    def test_a_missing_session_is_a_404(self, forge):
        response = forge.client.get(
            f"{PREFIX}/sessions/00000000-0000-0000-0000-000000000000/participant-candidates",
            headers=_headers("owner"),
        )
        assert response.status_code == 404
