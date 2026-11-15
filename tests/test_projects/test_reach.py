"""Members are told: gates, permission prompts and questions reach everyone who may answer.

An item that asks someone to act goes to the session's owner and to each approver of the
session (project approvers and owners, and per-session approvers), one row each, and
settles for all of them together. Viewers and contributors are not told, and a session with
no project tells only its owner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from niuu.domain.notifications import NotificationDraft
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from tests.test_projects.test_members import ALICE, BOB, CAROL, World, person
from volundr.domain.models import EventType
from volundr.domain.notifications import (
    NotificationNotFoundError,
    NotificationQuery,
    NotificationRule,
    NotificationRuleMatch,
    NotificationSinkInfo,
)
from volundr.domain.projects import ProjectRole
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.room_grants import RoomGrantSource
from volundr.domain.session_participants import ParticipantRole

OWNER = person("owner")
FRANK = person("frank")
GINA = person("gina")
SINCE = datetime(2026, 11, 10, 12, tzinfo=UTC)


@pytest.fixture
async def world():
    w = World()
    w.users.seed("frank", "acme")
    w.users.seed("gina", "acme")
    await w.create_project()
    await w.add("bob", ProjectRole.APPROVER)
    await w.add("carol", ProjectRole.VIEWER)
    await w.add("frank", ProjectRole.CONTRIBUTOR)
    return w


def build(world, *, grants=True, broadcaster=None):
    store = InMemoryNotificationStore()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=True,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        room_grants=(
            RoomGrantSource(world.participants._repository, world.members) if grants else None
        ),
    )
    return service, store


async def rows(service, principal, **query):
    feed = await service.list_feed(principal, NotificationQuery(limit=50, **query))
    return feed.items


async def ask(service, session, *, key="gate-1", kind="attention", **draft):
    values = {"kind": kind, "title": "Approve the deploy?", "severity": "warning", **draft}
    original, _ = await service.submit(
        session, OWNER, NotificationDraft(**values), idempotency_key=key
    )
    return original


class TestFanOut:
    async def test_a_gate_reaches_the_owner_and_every_approver_and_nobody_else(self, world):
        service, store = build(world)
        session = await world.session()

        original = await ask(service, session)

        assert original.owner_id == "owner" and original.copy_of is None
        holders = {n.owner_id: n for n in store.notifications.values()}
        assert set(holders) == {"owner", "alice", "bob"}  # alice owns the project
        for person_id in ("alice", "bob"):
            copy = holders[person_id]
            assert copy.copy_of == original.id
            assert copy.id != original.id
            assert (copy.kind, copy.title, copy.severity) == (
                original.kind,
                original.title,
                original.severity,
            )
            assert (copy.session_id, copy.tenant_id, copy.project_id) == (
                original.session_id,
                original.tenant_id,
                original.project_id,
            )
        assert "carol" not in holders and "frank" not in holders

    async def test_viewers_and_contributors_are_told_nothing(self, world):
        service, _ = build(world)
        session = await world.session()
        await ask(service, session)
        for viewer in (CAROL, FRANK):
            assert await rows(service, viewer) == []
            assert (await service.get_read_state(viewer)).head_seq == 0

    async def test_each_kind_that_asks_is_fanned_out_and_the_rest_are_not(self, world):
        service, store = build(world)
        session = await world.session()
        for index, (kind, extra) in enumerate(
            [
                ("attention", {}),
                ("decision", {}),
                ("info", {"question": True}),
                ("milestone", {}),
                ("info", {}),
                ("error", {}),
            ]
        ):
            await ask(service, session, key=f"k{index}", kind=kind, title=f"t{index}", **extra)
        per_title = {}
        for n in store.notifications.values():
            per_title.setdefault(n.title, set()).add(n.owner_id)
        everyone = {"owner", "alice", "bob"}
        assert per_title == {
            "t0": everyone,
            "t1": everyone,
            "t2": everyone,
            "t3": {"owner"},
            "t4": {"owner"},
            "t5": {"owner"},
        }

    async def test_a_permission_prompt_and_a_question_reach_approvers_as_attention(self, world):
        service, store = build(world)
        session = await world.session()

        permission = await service.record_attention(
            session, state_since=SINCE, kind="permission", prompt="Run rm?", request_id="p1"
        )
        question = await service.record_attention(
            session, state_since=SINCE, kind="question", prompt="Which DB?", request_id="q1"
        )

        assert permission.owner_id == "owner" and permission.copy_of is None
        assert question.owner_id == "owner" and question.copy_of is None
        for approver in (ALICE, BOB):
            mine = await rows(service, approver)
            assert {n.input_request["kind"] for n in mine} == {"permission", "question"}
            assert {n.body for n in mine} == {"Run rm?", "Which DB?"}
            assert {n.copy_of for n in mine} == {permission.id, question.id}
        assert len(store.notifications) == 6

    async def test_the_owner_can_be_a_project_approver_without_a_second_row(self, world):
        service, store = build(world)
        await world.add("owner", ProjectRole.OWNER)
        session = await world.session()
        await ask(service, session)
        assert sorted(n.owner_id for n in store.notifications.values()) == [
            "alice",
            "bob",
            "owner",
        ]

    async def test_per_session_approvers_are_told_and_per_session_viewers_are_not(self, world):
        service, store = build(world)
        session = await world.session(project=False)
        await world.participants.invite(
            session, OWNER, user_id="gina", role=ParticipantRole.APPROVER, expires_at=None
        )
        await world.participants.accept(session, GINA)
        await world.participants.invite(
            session, OWNER, user_id="carol", role=ParticipantRole.VIEWER, expires_at=None
        )
        await world.participants.accept(session, CAROL)

        await ask(service, session)

        assert sorted(n.owner_id for n in store.notifications.values()) == ["gina", "owner"]
        assert len(await rows(service, GINA)) == 1
        assert await rows(service, CAROL) == []

    async def test_an_invitation_not_yet_accepted_tells_nobody(self, world):
        service, store = build(world)
        session = await world.session(project=False)
        await world.participants.invite(
            session, OWNER, user_id="gina", role=ParticipantRole.APPROVER, expires_at=None
        )
        await ask(service, session)
        assert [n.owner_id for n in store.notifications.values()] == ["owner"]

    async def test_a_session_without_a_project_or_guests_is_unchanged(self, world):
        service, store = build(world)
        session = await world.session(project=False)

        original = await ask(service, session)
        await service.record_attention(
            session, state_since=SINCE, kind="question", prompt="?", request_id="q1"
        )

        assert {n.owner_id for n in store.notifications.values()} == {"owner"}
        assert len(store.notifications) == 2
        assert original.wire()["copy_of"] is None

    async def test_other_tenants_members_are_never_told(self, world):
        from volundr.domain.projects import ProjectMember

        await world.members.add(
            ProjectMember(
                project_id=world.project.id,
                user_id="dave",
                tenant_id="other",
                role=ProjectRole.OWNER,
            )
        )
        service, store = build(world)
        await ask(service, await world.session())
        assert "dave" not in {n.owner_id for n in store.notifications.values()}

    async def test_a_service_without_room_roles_tells_only_the_owner(self, world):
        service, store = build(world, grants=False)
        await ask(service, await world.session())
        assert [n.owner_id for n in store.notifications.values()] == ["owner"]

    async def test_a_retry_does_not_tell_someone_who_became_an_approver_since(self, world):
        service, store = build(world)
        session = await world.session()
        await ask(service, session)
        await world.service.member_service.change(
            world.project.id, ALICE, "carol", role=ProjectRole.APPROVER
        )

        again = await ask(service, session)  # the producer retries the same key

        assert again.copy_of is None
        assert sorted(n.owner_id for n in store.notifications.values()) == [
            "alice",
            "bob",
            "owner",
        ]
        assert await rows(service, CAROL) == []

    async def test_the_producer_hears_of_the_original_only(self, world):
        service, _ = build(world)
        session = await world.session()
        created = await service.record_attention(
            session, state_since=SINCE, kind="question", prompt="?", request_id="q1"
        )
        assert created.owner_id == "owner" and created.copy_of is None

    async def test_every_copy_is_broadcast_to_its_own_recipient(self, world):
        broadcaster = MockEventBroadcaster()
        service, _ = build(world, broadcaster=broadcaster)
        await ask(service, await world.session())
        events = [e for e in broadcaster._events if e.type is EventType.SESSION_NOTIFICATION]
        assert sorted(e.data["owner_id"] for e in events) == ["alice", "bob", "owner"]
        assert sorted(e.data["copy_of"] is None for e in events) == [False, False, True]

    async def test_a_member_is_delivered_through_their_own_rules(self, world):
        service, store = build(world)
        bob_rule = await store.rule_repo.create(_rule("bob"))
        await store.rule_repo.create(_rule("carol"))
        await ask(service, await world.session())
        assert [d.rule_id for d in store.deliveries.values()] == [bob_rule.id]


def _rule(owner):
    now = datetime.now(UTC)
    return NotificationRule(
        id=uuid4(),
        owner_id=owner,
        name="r",
        sink="ops",
        match=NotificationRuleMatch(),
        created_at=now,
        updated_at=now,
    )


class TestVisibility:
    async def test_a_member_sees_and_reads_their_own_copy_only(self, world):
        service, _ = build(world)
        original = await ask(service, await world.session())

        [mine] = await rows(service, BOB)
        assert mine.copy_of == original.id and mine.owner_id == "bob"
        assert (await service.get_read_state(BOB)).unread_count == 1
        assert (await service.get_read_state(OWNER)).unread_count == 1

        await service.mark_read(BOB, mine.id)

        assert (await service.get_read_state(BOB)).unread_count == 0
        assert (await service.get_read_state(OWNER)).unread_count == 1  # the owner's is theirs
        with pytest.raises(NotificationNotFoundError):
            await service.mark_read(CAROL, mine.id)
        with pytest.raises(NotificationNotFoundError):
            await service.mark_read(BOB, original.id)

    async def test_the_api_names_the_original_on_a_copy(self, world):
        from volundr.adapters.inbound.rest_notifications import NotificationResponse

        service, _ = build(world)
        original = await ask(service, await world.session())
        [mine] = await rows(service, BOB)

        assert NotificationResponse.build(mine, 0).copy_of == original.id
        assert NotificationResponse.build(original, 0).copy_of is None

    async def test_an_admin_sees_the_item_once_not_once_per_recipient(self, world):
        service, _ = build(world)
        original = await ask(service, await world.session())

        seen = await rows(service, person("root", roles=("volundr:admin",)))

        assert [n.id for n in seen] == [original.id]

    async def test_an_admin_who_is_also_a_recipient_sees_their_own_copy_too(self, world):
        service, _ = build(world)
        original = await ask(service, await world.session())
        world.users.seed("root", "acme")
        await world.add("root", ProjectRole.APPROVER)
        original2 = await ask(service, await world.session(), key="gate-2")
        admin = person("root", roles=("volundr:admin",))

        seen = await rows(service, admin)

        assert {n.copy_of for n in seen if n.owner_id == "root"} == {original2.id}
        assert original.id in {n.id for n in seen}

    async def test_the_session_feed_shows_each_person_one_row_of_the_item(self, world):
        service, _ = build(world)
        session = await world.session()
        original = await ask(service, session)

        owner_view, _ = await service.list_for_session(OWNER, session.id, after=0, limit=10)
        bob_view, _ = await service.list_for_session(BOB, session.id, after=0, limit=10)
        carol_view, _ = await service.list_for_session(CAROL, session.id, after=0, limit=10)

        assert [n.id for n in owner_view] == [original.id]
        assert [(n.owner_id, n.copy_of) for n in bob_view] == [("bob", original.id)]
        assert [n.id for n in carol_view] == [original.id]  # not a recipient: the original


class TestSettlement:
    async def test_the_owner_resolving_settles_every_recipients_row(self, world):
        broadcaster = MockEventBroadcaster()
        service, store = build(world, broadcaster=broadcaster)
        original = await ask(service, await world.session())
        broadcaster._events.clear()

        await service.resolve(OWNER, original.id, resolution="Approved by owner")

        assert all(n.resolved for n in store.notifications.values())
        assert {n.resolution for n in store.notifications.values()} == {"Approved by owner"}
        for approver in (ALICE, BOB):
            assert (await rows(service, approver, unresolved=True)) == []
        resolved_events = [e.data for e in broadcaster._events]
        assert sorted(e["owner_id"] for e in resolved_events) == ["alice", "bob", "owner"]
        assert all(e["resolved"] for e in resolved_events)

    async def test_a_member_resolving_their_copy_settles_everyones(self, world):
        service, store = build(world)
        original = await ask(service, await world.session())
        [bobs] = await rows(service, BOB)

        resolved = await service.resolve(BOB, bobs.id, resolution="Approved")

        assert resolved.id == bobs.id and resolved.resolved
        assert store.notifications_by_id(original.id).resolved
        assert all(n.resolved for n in store.notifications.values())
        assert await rows(service, OWNER, unresolved=True) == []

    async def test_resolving_again_changes_nothing(self, world):
        broadcaster = MockEventBroadcaster()
        service, store = build(world, broadcaster=broadcaster)
        original = await ask(service, await world.session())
        await service.resolve(OWNER, original.id, resolution="first")
        broadcaster._events.clear()

        await service.resolve(BOB, (await rows(service, BOB))[0].id, resolution="second")

        assert broadcaster._events == []
        assert {n.resolution for n in store.notifications.values()} == {"first"}

    async def test_a_second_call_finishes_a_group_left_half_settled(self, world):
        service, store = build(world)
        original = await ask(service, await world.session())
        # An earlier call resolved the original, then died before the copies.
        await store.feed.resolve(
            "owner",
            [original.id],
            resolved_at=SINCE,
            resolved_by=None,
            resolution="started",
        )
        assert [n.resolved for n in store.notifications.values()].count(True) == 1

        await service.resolve(OWNER, original.id, resolution="again")

        assert all(n.resolved for n in store.notifications.values())

    async def test_a_later_notice_that_resolves_the_item_settles_the_group(self, world):
        service, store = build(world)
        session = await world.session()
        original = await ask(service, session)

        await service.submit(
            session,
            OWNER,
            NotificationDraft(
                kind="milestone", title="Deploy approved", resolves=[str(original.id)]
            ),
            idempotency_key="done",
        )

        gate_rows = [n for n in store.notifications.values() if n.kind.value == "attention"]
        assert len(gate_rows) == 3 and all(n.resolved for n in gate_rows)

    async def test_a_milestone_for_the_incident_settles_every_copy(self, world):
        service, store = build(world)
        session = await world.session()
        await ask(service, session, correlation_id="deploy-7")

        await service.submit(
            session,
            OWNER,
            NotificationDraft(kind="milestone", title="Deployed", correlation_id="deploy-7"),
            idempotency_key="shipped",
        )

        gate_rows = [n for n in store.notifications.values() if n.kind.value == "attention"]
        assert len(gate_rows) == 3 and all(n.resolved for n in gate_rows)

    async def test_the_question_being_answered_retires_it_for_everyone(self, world):
        service, _ = build(world)
        session = await world.session()
        await service.record_attention(
            session, state_since=SINCE, kind="permission", prompt="Run rm?", request_id="p1"
        )
        for who in (OWNER, ALICE, BOB):
            assert (await service.get_read_state(who)).unread_count == 1

        assert await service.retire_attention(session) == 3

        for who in (OWNER, ALICE, BOB):
            assert (await service.get_read_state(who)).unread_count == 0
        assert await service.retire_attention(session) == 0

    async def test_retiring_leaves_a_member_who_already_read_theirs_alone(self, world):
        service, _ = build(world)
        session = await world.session()
        await service.record_attention(
            session, state_since=SINCE, kind="question", prompt="?", request_id="q1"
        )
        [bobs] = await rows(service, BOB)
        await service.mark_read(BOB, bobs.id)

        assert await service.retire_attention(session) == 2  # the owner's and alice's

    async def test_retiring_a_session_without_a_project_still_only_has_the_owner(self, world):
        service, _ = build(world)
        session = await world.session(project=False)
        await service.record_attention(
            session, state_since=SINCE, kind="question", prompt="?", request_id="q1"
        )
        assert await service.retire_attention(session) == 1

    async def test_retirement_pages_through_each_recipients_items(self, world):
        store = InMemoryNotificationStore()
        service = NotificationService(
            store.feed,
            store.rule_repo,
            store.outbox,
            image_repository=store.images,
            image_scheduler=store.image_scheduler,
            reply_ready_enabled=True,
            reply_title_chars=40,
            reply_body_chars=60,
            sinks=[],
            attention_retire_page_size=2,
            room_grants=RoomGrantSource(world.participants._repository, world.members),
        )
        session = await world.session()
        for index in range(4):
            await service.record_attention(
                session, state_since=SINCE, kind="question", prompt="?", request_id=f"q{index}"
            )
        assert await service.retire_attention(session) == 12  # 4 items x 3 recipients
        for who in (OWNER, ALICE, BOB):
            assert (await service.get_read_state(who)).unread_count == 0


def test_the_copy_marker_reads_back_as_the_originals_id():
    original = uuid4()
    from volundr.domain.notifications import COPY_OF_KEY, Notification

    base = {
        "id": uuid4(),
        "seq": 1,
        "dedupe_key": "k",
        "owner_id": "u",
        "kind": "attention",
        "severity": "warning",
        "source": "operator",
        "title": "t",
        "created_at": SINCE,
    }
    assert Notification(**base).copy_of is None
    copy = Notification(**base, metadata={COPY_OF_KEY: str(original)})
    assert copy.copy_of == original
    assert copy.wire()["copy_of"] == str(original)


# --- find_people: who is on the session's project --------------------------------------


@pytest.fixture
async def people_client(world, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from volundr.adapters.inbound import rest_session_people

    async def principal_for_request(request):
        user = request.headers["x-user"]
        roles = ("volundr:admin",) if user == "root" else ("volundr:developer",)
        return person(user, tenant="other" if user == "dave" else "acme", roles=roles)

    monkeypatch.setattr(rest_session_people, "extract_principal", principal_for_request)
    app = FastAPI()
    app.include_router(
        rest_session_people.create_session_people_router(world.participants, world.sessions)
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        yield c


async def people_of(client, session, user):
    return await client.get(f"/api/v1/forge/sessions/{session.id}/people", headers={"x-user": user})


class TestPeopleRoute:
    async def test_the_owner_and_every_project_member_may_list(self, world, people_client):
        session = await world.session()
        for user in ("owner", "alice", "bob", "carol", "frank"):
            response = await people_of(people_client, session, user)
            assert response.status_code == 200, user
            assert [p["user_id"] for p in response.json()] == ["alice", "bob", "carol", "frank"]

    async def test_roles_and_gate_authority_come_from_the_project(self, world, people_client):
        session = await world.session()
        response = await people_of(people_client, session, "owner")
        assert {p["user_id"]: (p["role"], p["can_answer_gates"]) for p in response.json()} == {
            "alice": ("owner", True),
            "bob": ("approver", True),
            "carol": ("viewer", False),
            "frank": ("contributor", False),
        }

    async def test_expertise_and_skills_travel_with_the_member(self, world, people_client):
        from niuu.domain.agent_directory import AgentSkill

        await world.service.member_service.change(
            world.project.id,
            ALICE,
            "carol",
            expertise="Billing and invoices",
            skills=[AgentSkill(id="invoicing", name="Invoicing", tags=["finance"])],
        )
        session = await world.session()
        response = await people_of(people_client, session, "owner")
        carol = next(p for p in response.json() if p["user_id"] == "carol")
        assert carol["expertise"] == "Billing and invoices"
        assert carol["skills"][0]["id"] == "invoicing" and carol["skills"][0]["tags"] == ["finance"]

    async def test_a_per_session_participant_may_list(self, world, people_client):
        session = await world.session()
        await world.participants.invite(
            session, OWNER, user_id="gina", role=ParticipantRole.VIEWER, expires_at=None
        )
        await world.participants.accept(session, GINA)
        assert (await people_of(people_client, session, "gina")).status_code == 200

    async def test_someone_outside_the_room_is_refused(self, world, people_client):
        session = await world.session()
        assert (await people_of(people_client, session, "gina")).status_code == 403
        assert (await people_of(people_client, session, "dave")).status_code == 403

    async def test_a_tenant_admin_may_list(self, world, people_client):
        session = await world.session()
        assert (await people_of(people_client, session, "root")).status_code == 200

    async def test_a_session_with_no_project_is_a_409_that_says_so(self, world, people_client):
        session = await world.session(project=False)
        response = await people_of(people_client, session, "owner")
        assert response.status_code == 409
        assert "not attached to a project" in response.json()["detail"]

    async def test_an_unknown_session_is_404(self, world, people_client):
        response = await people_client.get(
            f"/api/v1/forge/sessions/{uuid4()}/people", headers={"x-user": "owner"}
        )
        assert response.status_code == 404

    async def test_a_session_credential_bound_to_another_session_is_refused(
        self, world, monkeypatch
    ):
        import httpx
        from fastapi import FastAPI

        from niuu.domain.models import Principal
        from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
        from volundr.adapters.inbound import rest_session_people

        session = await world.session()
        credential = Principal(
            user_id="owner",
            email="",
            tenant_id="acme",
            roles=["volundr:developer"],
            token_use=FORGE_SESSION_TOKEN_USE,
            bound_session_id=str(uuid4()),
        )

        async def principal_for_request(request):
            return credential

        monkeypatch.setattr(rest_session_people, "extract_principal", principal_for_request)
        app = FastAPI()
        app.include_router(
            rest_session_people.create_session_people_router(world.participants, world.sessions)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/forge/sessions/{session.id}/people")
        assert response.status_code == 403
        assert "bound to another session" in response.json()["detail"]

    async def test_only_the_members_of_the_sessions_own_tenant_are_listed(
        self, world, people_client
    ):
        from volundr.domain.projects import ProjectMember

        await world.members.add(
            ProjectMember(
                project_id=world.project.id,
                user_id="dave",
                tenant_id="other",
                role=ProjectRole.OWNER,
                expertise="not ours",
            )
        )
        session = await world.session()
        response = await people_of(people_client, session, "owner")
        assert "dave" not in [p["user_id"] for p in response.json()]
