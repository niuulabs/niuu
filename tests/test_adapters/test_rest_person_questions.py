"""``POST /sessions/{id}/person-questions``: a session's agent asks one person of its project.

Forge checks the person is on the project, records one notification for them alone (open
until answered) and, when the broker reports who answered, settles it. Only the session's own
credential may do either.
"""

from __future__ import annotations

import uuid

import pytest

from niuu.domain.person_question import person_question_id
from niuu.domain.services.forge_session_policy import policy_refusal
from niuu.domain.services.token_scope import FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer, build_forge_app
from tests.test_projects.conftest import MemoryProjectMembers
from volundr.domain.projects import ProjectMember, ProjectRole, SessionCoordination

PROJECT = uuid.uuid4()
QUESTION = "Which API key do we use for staging?"


def _headers(user: str) -> dict[str, str]:
    return {"x-auth-user-id": user, "x-auth-tenant": TENANT}


@pytest.fixture
async def forge():
    members = MemoryProjectMembers()
    for user_id, role in (
        ("alice", ProjectRole.VIEWER),
        ("bob", ProjectRole.APPROVER),
        ("carol", ProjectRole.OWNER),
    ):
        await members.add(
            ProjectMember(
                project_id=PROJECT,
                user_id=user_id,
                tenant_id=TENANT,
                role=role,
                display_name=user_id.title(),
            )
        )
    return build_forge_app(project_members=members)


async def _own_session(forge, *, project=True):
    extra = {"coordination": SessionCoordination(project_id=PROJECT)} if project else {}
    session = await forge.session("own", **extra)
    credential = await forge.start(session)
    return session, credential.token


def _ask(forge, session, token, *, user_id="alice", request_id="req-1", question=QUESTION):
    return forge.client.post(
        f"{PREFIX}/sessions/{session.id}/person-questions",
        json={"user_id": user_id, "question": question, "request_id": request_id},
        headers=bearer(token),
    )


def _feed(forge, user: str, **params) -> list[dict]:
    response = forge.client.get(f"{PREFIX}/notifications", headers=_headers(user), params=params)
    assert response.status_code == 200
    return response.json()["items"]


class TestAsking:
    async def test_the_member_is_registered_and_told_with_one_row_of_their_own(self, forge):
        session, token = await _own_session(forge)

        response = _ask(forge, session, token)

        assert response.status_code == 201
        body = response.json()
        assert body["question_id"] == person_question_id(str(session.id), "alice", "req-1")
        assert body["addressee"] == {
            "user_id": "alice",
            "display_name": "Alice",
            "role": "viewer",
            "expertise": "",
            "skills": [],
            "can_answer_gates": False,
        }
        [row] = _feed(forge, "alice")
        assert row["id"] == body["notification_id"]
        assert (row["kind"], row["owner_id"], row["source"]) == ("decision", "alice", "agent")
        assert (row["question"], row["body"], row["session_id"]) == (
            True,
            QUESTION,
            str(session.id),
        )
        assert row["resolved"] is False and row["copy_of"] is None

    async def test_nobody_else_is_told_not_the_owner_and_not_the_approvers(self, forge):
        session, token = await _own_session(forge)
        _ask(forge, session, token)
        for user in (OWNER, "bob", "carol"):
            assert _feed(forge, user) == []

    async def test_a_viewer_can_be_asked_whatever_their_role(self, forge):
        session, token = await _own_session(forge)
        assert _ask(forge, session, token, user_id="alice").status_code == 201
        assert _ask(forge, session, token, user_id="bob").status_code == 201
        assert _ask(forge, session, token, user_id="carol").status_code == 201

    async def test_asking_again_with_the_same_request_id_is_the_same_question(self, forge):
        session, token = await _own_session(forge)
        first = _ask(forge, session, token)
        again = _ask(forge, session, token)
        assert again.status_code == 200
        assert again.json()["notification_id"] == first.json()["notification_id"]
        assert again.json()["question_id"] == first.json()["question_id"]
        assert len(_feed(forge, "alice")) == 1

    async def test_a_different_request_id_is_a_new_question(self, forge):
        session, token = await _own_session(forge)
        first = _ask(forge, session, token, request_id="req-1")
        second = _ask(forge, session, token, request_id="req-2")
        assert second.status_code == 201
        assert second.json()["question_id"] != first.json()["question_id"]
        assert len(_feed(forge, "alice")) == 2

    async def test_a_person_who_is_not_on_the_project_is_refused_with_the_remedy(self, forge):
        session, token = await _own_session(forge)

        response = _ask(forge, session, token, user_id="mallory")

        assert response.status_code == 422
        assert "not a member of this session's project" in response.json()["detail"]
        assert "find_people" in response.json()["detail"]
        assert _feed(forge, "mallory") == []

    async def test_a_session_with_no_project_is_a_409_that_says_so(self, forge):
        session, token = await _own_session(forge, project=False)

        response = _ask(forge, session, token)

        assert response.status_code == 409
        assert "not attached to a project" in response.json()["detail"]
        assert _feed(forge, "alice") == []

    async def test_a_forge_without_notifications_says_how_to_fix_it(self, forge):
        members = forge.participants._project_members
        off = build_forge_app(project_members=members, no_notifications=True)
        session, token = await _own_session(off)
        response = _ask(off, session, token)
        assert response.status_code == 503 and "notifications.enabled" in response.json()["detail"]

    async def test_the_question_and_the_ids_are_validated(self, forge):
        session, token = await _own_session(forge)
        path = f"{PREFIX}/sessions/{session.id}/person-questions"
        for body in (
            {"user_id": "alice"},
            {"user_id": "", "question": "x", "request_id": "r"},
            {"user_id": "alice", "question": "x", "request_id": "bad id!"},
            {"user_id": "alice", "question": "x" * 4001, "request_id": "r"},
            {"user_id": "alice", "question": "x", "request_id": "r", "extra": 1},
        ):
            response = forge.client.post(path, json=body, headers=bearer(token))
            assert response.status_code == 422, body


class TestWhoMayAsk:
    async def test_only_the_session_itself_may_ask(self, forge):
        session, _ = await _own_session(forge)
        response = forge.client.post(
            f"{PREFIX}/sessions/{session.id}/person-questions",
            json={"user_id": "alice", "question": QUESTION, "request_id": "r"},
            headers=_headers(OWNER),
        )
        assert response.status_code == 403
        assert "Only the session itself" in response.json()["detail"]
        assert _feed(forge, "alice") == []

    async def test_another_sessions_credential_is_refused(self, forge):
        _, token = await _own_session(forge)
        peer = await forge.session("peer", coordination=SessionCoordination(project_id=PROJECT))
        response = _ask(forge, peer, token)
        assert response.status_code == 403
        assert "bound to another session" in response.json()["detail"]

    def test_the_routes_need_the_notify_scope_and_the_session_binding(self):
        base = f"{PREFIX}/sessions/{uuid.uuid4()}/person-questions"
        for method, path in (("POST", base), ("POST", f"{base}/{uuid.uuid4()}/answer")):
            assert policy_refusal(method, path, (FORGE_NOTIFY_SCOPE,)) is None
            refusal = policy_refusal(method, path, (FORGE_SESSION_READ_SCOPE,))
            assert refusal is not None and FORGE_NOTIFY_SCOPE in refusal
            assert policy_refusal("GET", path, (FORGE_NOTIFY_SCOPE,)) is not None
            assert policy_refusal("DELETE", path, (FORGE_NOTIFY_SCOPE,)) is not None


class TestSettling:
    def _answer(self, forge, session, token, question_id, by="bob", name="Bob"):
        return forge.client.post(
            f"{PREFIX}/sessions/{session.id}/person-questions/{question_id}/answer",
            json={"answered_by_user_id": by, "answered_by_name": name},
            headers=bearer(token),
        )

    async def test_the_answer_settles_the_addressees_notification_for_good(self, forge):
        session, token = await _own_session(forge)
        asked = _ask(forge, session, token).json()

        response = self._answer(forge, session, token, asked["question_id"], by="bob", name="Bob")

        assert response.status_code == 200
        assert response.json() == {
            "question_id": asked["question_id"],
            "notification_id": asked["notification_id"],
            "resolved": True,
        }
        [row] = _feed(forge, "alice", unresolved="false")
        assert row["resolved"] is True and row["resolution"] == "Answered by Bob."
        assert _feed(forge, "alice", unresolved="true") == []

    async def test_settling_twice_keeps_the_first_resolution(self, forge):
        session, token = await _own_session(forge)
        asked = _ask(forge, session, token).json()
        self._answer(forge, session, token, asked["question_id"], name="Bob")
        self._answer(forge, session, token, asked["question_id"], by="carol", name="Carol")
        [row] = _feed(forge, "alice", unresolved="false")
        assert row["resolution"] == "Answered by Bob."

    async def test_a_question_never_put_to_a_person_is_a_404(self, forge):
        session, token = await _own_session(forge)
        response = self._answer(forge, session, token, str(uuid.uuid4()))
        assert response.status_code == 404

    async def test_a_person_cannot_mark_a_question_answered(self, forge):
        session, token = await _own_session(forge)
        asked = _ask(forge, session, token).json()
        response = forge.client.post(
            f"{PREFIX}/sessions/{session.id}/person-questions/{asked['question_id']}/answer",
            json={"answered_by_user_id": "alice", "answered_by_name": "Alice"},
            headers=_headers(OWNER),
        )
        assert response.status_code == 403
        assert _feed(forge, "alice", unresolved="true")

    async def test_a_forge_without_notifications_says_how_to_fix_it(self, forge):
        off = build_forge_app(no_notifications=True)
        session, token = await _own_session(off)
        response = self._answer(off, session, token, str(uuid.uuid4()))
        assert response.status_code == 503
