"""REST contract for Forge notifications (contract §4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.notifications import InMemoryNotificationStore
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.domain.models import Session, SessionLogEntry
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.ports import AuthorizationPort
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
CACHE_MAX_AGE = 3600


class OwnerOnly(AuthorizationPort):
    async def is_allowed(self, principal, action, resource):
        return (
            "volundr:admin" in principal.roles or resource.attr.get("owner_id") == principal.user_id
        )

    async def filter_allowed(self, principal, action, resources):
        return [r for r in resources if await self.is_allowed(principal, action, r)]


def headers(user: str = "owner-a", *, admin: bool = False) -> dict[str, str]:
    return {
        "x-auth-user-id": user,
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "volundr:admin" if admin else "volundr:developer",
    }


@pytest.fixture
def env():
    store = InMemoryNotificationStore()
    sessions = InMemorySessionRepository()
    session_service = SessionService(sessions, MockPodManager(), authorization=OwnerOnly())
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        reply_ready_enabled=True,
        reply_title_chars=80,
        reply_body_chars=100,
        sinks=[NotificationSinkInfo(name="ops", label="Ops webhook")],
    )
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()  # trusts forwarded x-auth-*
    app.include_router(
        create_notifications_router(
            service,
            session_service,
            prefix=PREFIX,
            default_page_size=2,
            max_page_size=5,
            image_cache_max_age_seconds=CACHE_MAX_AGE,
        )
    )
    return TestClient(app), service, store, sessions


async def _seed(service, sessions, owner="owner-a", count=3) -> Session:
    session = Session(name=f"{owner}-s", owner_id=owner, tenant_id="tenant-a")
    await sessions.create(session)
    entries = []
    for seq in range(1, count + 1):
        turn = build_notification_turn(
            NotificationDraft(kind="milestone", title=f"m{seq}"),
            turn_id=f"t{seq}",
            session_id=session.id,
            created_at="2026-09-23T10:00:00Z",
        )
        entries.append(
            SessionLogEntry(
                session_id=session.id,
                seq=seq,
                kind="conversation.turn",
                payload={"type": "conversation.turn", "turn": turn},
                ts=datetime.now(UTC),
            )
        )
    await service.project_log_entries(session, entries)
    return session


class TestFeed:
    async def test_pages_newest_first_with_next_before(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions)
        await _seed(service, sessions, owner="owner-b", count=1)

        first = client.get(f"{PREFIX}/notifications", headers=headers()).json()
        assert [item["title"] for item in first["items"]] == ["m3", "m2"]
        assert first["next_before"] == first["items"][-1]["seq"]
        assert (first["head_seq"], first["read_through_seq"], first["unread_count"]) == (3, 0, 3)
        assert first["items"][0]["read"] is False
        assert "dedupe_key" not in first["items"][0]
        assert first["items"][0]["turn_id"] == "t3"  # the transcript anchor for deep links

        second = client.get(
            f"{PREFIX}/notifications",
            params={"before": first["next_before"]},
            headers=headers(),
        ).json()
        assert [item["title"] for item in second["items"]] == ["m1"]
        assert second["next_before"] is None

        gap = client.get(
            f"{PREFIX}/notifications", params={"after": 1, "limit": 5}, headers=headers()
        ).json()
        assert [item["seq"] for item in gap["items"]] == [2, 3]

        admin = client.get(
            f"{PREFIX}/notifications", params={"limit": 5}, headers=headers("root", admin=True)
        ).json()
        assert len(admin["items"]) == 4

    async def test_filters_are_parsed_and_validated(self, env):
        client, service, _, sessions = env
        session = await _seed(service, sessions)
        ok = client.get(
            f"{PREFIX}/notifications",
            params={
                "kind": "milestone, decision,,milestone",
                "source": "agent",
                "min_severity": "info",
                "session_id": str(session.id),
                "unread": "true",
            },
            headers=headers(),
        )
        assert ok.status_code == 200 and len(ok.json()["items"]) == 2
        for params in (
            {"kind": "nope"},
            {"source": "robot"},
            {"min_severity": "loud"},
            {"before": 3, "after": 1},
            {"limit": 6},
            {"limit": 0},
            {"before": 0},
        ):
            response = client.get(f"{PREFIX}/notifications", params=params, headers=headers())
            assert response.status_code == 422, params

    def test_requires_identity(self, env):
        client, *_ = env
        assert client.get(f"{PREFIX}/notifications").status_code == 401


class TestReadState:
    async def test_cas_forward_only_conflict_and_future(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions)
        url = f"{PREFIX}/notifications/read-state"
        assert client.get(url, headers=headers()).json() == {
            "read_through_seq": 0,
            "revision": 0,
            "unread_count": 3,
            "head_seq": 3,
        }
        moved = client.put(
            url, json={"read_through_seq": 2, "expected_revision": 0}, headers=headers()
        )
        assert moved.json() == {
            "read_through_seq": 2,
            "revision": 1,
            "unread_count": 1,
            "head_seq": 3,
        }
        stale = client.put(
            url, json={"read_through_seq": 3, "expected_revision": 0}, headers=headers()
        )
        assert stale.status_code == 409
        future = client.put(
            url, json={"read_through_seq": 9, "expected_revision": 1}, headers=headers()
        )
        assert future.status_code == 422
        bad = client.put(
            url, json={"read_through_seq": -1, "expected_revision": 1}, headers=headers()
        )
        assert bad.status_code == 422
        feed = client.get(f"{PREFIX}/notifications", headers=headers()).json()
        assert [item["read"] for item in feed["items"]] == [False, True]


class TestRulesSinksDeliveries:
    def test_rule_crud(self, env):
        client, *_ = env
        url = f"{PREFIX}/notifications/rules"
        body = {
            "name": "ops",
            "match": {"kinds": ["decision"], "min_severity": "warning"},
            "sink": "ops",
            "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Europe/London"},
        }
        created = client.post(url, json=body, headers=headers())
        assert created.status_code == 201
        rule = created.json()
        assert rule["quiet_hours"]["allow_min_severity"] == "critical"
        assert "owner_id" not in rule
        assert [r["id"] for r in client.get(url, headers=headers()).json()] == [rule["id"]]
        assert client.get(url, headers=headers("owner-b")).json() == []

        replaced = client.put(
            f"{url}/{rule['id']}", json={**body, "enabled": False}, headers=headers()
        )
        assert replaced.status_code == 200 and replaced.json()["enabled"] is False
        assert (
            client.put(f"{url}/{rule['id']}", json=body, headers=headers("owner-b")).status_code
            == 404
        )
        assert (
            client.put(
                f"{url}/{rule['id']}", json={**body, "sink": "x"}, headers=headers()
            ).status_code
            == 422
        )
        assert client.delete(f"{url}/{rule['id']}", headers=headers("owner-b")).status_code == 404
        assert client.delete(f"{url}/{rule['id']}", headers=headers()).status_code == 204
        assert client.get(url, headers=headers()).json() == []

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "r", "sink": "telegram"},
            {"name": "r", "sink": "ops", "extra": 1},
            {"name": "r", "sink": "ops", "match": {"kinds": ["loud"]}},
            {"name": "r", "sink": "ops", "quiet_hours": {"start": "22:00"}},
            {"name": "r", "sink": "ops", "config": {"rate_limit": {"max_count": 0}}},
        ],
    )
    def test_rule_validation(self, env, body):
        client, *_ = env
        response = client.post(f"{PREFIX}/notifications/rules", json=body, headers=headers())
        assert response.status_code == 422

    def test_sinks(self, env):
        client, *_ = env
        assert client.get(f"{PREFIX}/notifications/sinks", headers=headers()).json() == [
            {"name": "ops", "label": "Ops webhook", "requires_integration": False}
        ]

    async def test_deliveries_are_visible_to_owner_only(self, env):
        client, service, store, sessions = env
        client.post(
            f"{PREFIX}/notifications/rules", json={"name": "r", "sink": "ops"}, headers=headers()
        )
        await _seed(service, sessions, count=1)
        [notification] = store.notifications.values()
        url = f"{PREFIX}/notifications/{notification.id}/deliveries"
        [delivery] = client.get(url, headers=headers()).json()
        assert (delivery["status"], delivery["sink"], delivery["attempts"]) == ("pending", "ops", 0)
        assert client.get(url, headers=headers("owner-b")).status_code == 404


class TestSessionRoutes:
    async def test_list_session_notifications_uses_session_access(self, env):
        client, service, _, sessions = env
        session = await _seed(service, sessions)
        url = f"{PREFIX}/sessions/{session.id}/notifications"
        items = client.get(url, params={"after": 1}, headers=headers()).json()
        assert [item["session_seq"] for item in items] == [2, 3]
        assert client.get(url, headers=headers("owner-b")).status_code == 403
        assert client.get(url, headers=headers("root", admin=True)).status_code == 200
        missing = client.get(f"{PREFIX}/sessions/{uuid4()}/notifications", headers=headers())
        assert missing.status_code == 404

    async def test_direct_submit_is_idempotent(self, env):
        client, _, store, sessions = env
        session = Session(name="s", owner_id="owner-a", tenant_id="tenant-a")
        await sessions.create(session)
        url = f"{PREFIX}/sessions/{session.id}/notifications"
        body = {
            "kind": "decision",
            "severity": "warning",
            "title": "  Pick   a DB ",
            "links": [{"label": "RFC", "url": "https://example.com"}],
            "idempotency_key": "req-1",
        }
        created = client.post(url, json=body, headers=headers())
        assert created.status_code == 201
        payload = created.json()
        assert payload["title"] == "Pick a DB" and payload["source"] == "operator"
        assert payload["session_seq"] is None and payload["read"] is False
        assert payload["turn_id"] is None  # a direct submit has no transcript turn
        again = client.post(url, json=body, headers=headers())
        assert again.status_code == 200 and again.json()["id"] == payload["id"]
        assert len(store.notifications) == 1

        for bad in (
            {**body, "idempotency_key": ""},
            {k: v for k, v in body.items() if k != "idempotency_key"},
            {**body, "kind": "reply_ready"},
            {**body, "title": "   "},
        ):
            assert client.post(url, json=bad, headers=headers()).status_code == 422
        assert client.post(url, json=body, headers=headers("owner-b")).status_code == 403


class TestStructuredQuestions:
    @pytest.mark.parametrize("structured", [False, True], ids=["legacy", "structured"])
    async def test_submit_retry_and_feed_reads_preserve_question(self, env, structured):
        client, service, store, sessions = env
        session = await _seed(service, sessions, count=0)
        url = f"{PREFIX}/sessions/{session.id}/notifications"
        ask = {
            "items": [
                {
                    "id": "database",
                    "kind": "question",
                    "text": "Which database?",
                    "options": [{"label": "Postgres"}, {"label": "SQLite"}],
                    "allow_text": False,
                },
                {"id": "access", "kind": "action", "text": "Grant the test account access."},
            ]
        }
        body = {
            "kind": "attention",
            "title": "Input needed",
            "question": True,
            "idempotency_key": "structured-question-1",
            **({"ask": ask} if structured else {}),
        }
        created = client.post(url, json=body, headers=headers())
        assert created.status_code == 201
        retry = client.post(url, json=body, headers=headers())
        assert retry.status_code == 200
        assert retry.json() == created.json()
        [session_notice] = client.get(url, headers=headers()).json()
        [feed_notice] = client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"]
        assert session_notice == feed_notice == created.json()
        assert feed_notice["question"] is True
        assert feed_notice["ask"] == (ask if structured else None)
        assert len(store.notifications) == 1
        assert client.get(url, headers=headers("owner-b")).status_code == 403

    @pytest.mark.parametrize(
        "fields",
        [
            {"question": False, "ask": {"items": [{"id": "q", "kind": "question", "text": "Q"}]}},
            {"question": True, "ask": {"items": []}},
            {
                "question": True,
                "ask": {
                    "items": [{"id": "q", "kind": "question", "text": "Q", "allow_text": False}]
                },
            },
            {
                "question": True,
                "ask": {
                    "items": [
                        {"id": f"q{i}", "kind": "question", "text": "😀" * 2000} for i in range(5)
                    ]
                },
            },
        ],
        ids=["ask-without-question", "empty-items", "unanswerable", "utf8-size-limit"],
    )
    async def test_invalid_ask_is_422_before_persistence(self, env, fields):
        client, service, store, sessions = env
        session = await _seed(service, sessions, count=0)
        response = client.post(
            f"{PREFIX}/sessions/{session.id}/notifications",
            json={"kind": "attention", "title": "Input", "idempotency_key": "bad-ask", **fields},
            headers=headers(),
        )
        assert response.status_code == 422
        assert not store.notifications

    async def test_attention_item_serves_its_runtime_request(self, env):
        client, service, _, sessions = env
        session = await _seed(service, sessions, count=0)
        await service.record_attention(
            session,
            state_since=datetime(2026, 10, 26, 9, tzinfo=UTC),
            kind="question",
            prompt="Which DB?",
            request_id="toolu_01",
        )

        [item] = client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"]

        assert item["input_request"] == {"kind": "question", "request_id": "toolu_01"}
        assert item["question"] is None and item["ask"] is None


class TestIndividualReads:
    async def test_exact_read_receipt_feed_session_list_and_unread_filter(self, env):
        client, service, _, sessions = env
        session = await _seed(service, sessions)
        feed_url = f"{PREFIX}/notifications"
        items = client.get(feed_url, params={"limit": 5}, headers=headers()).json()["items"]
        nid = items[1]["id"]
        url = f"{feed_url}/{nid}/read"
        response = client.put(url, headers=headers())
        assert response.status_code == 200
        assert response.json() == {
            "id": nid,
            "read": True,
            "read_state": {"read_through_seq": 0, "head_seq": 3, "unread_count": 2, "revision": 1},
        }
        assert client.put(url, headers=headers()).json() == response.json()
        feed = client.get(feed_url, params={"limit": 5}, headers=headers()).json()
        assert [i["read"] for i in feed["items"]] == [False, True, False]
        assert feed["revision"] == 1 and feed["unread_count"] == 2
        unread = client.get(feed_url, params={"unread": True}, headers=headers()).json()
        assert [i["seq"] for i in unread["items"]] == [3, 1]
        ascending = client.get(
            f"{PREFIX}/sessions/{session.id}/notifications", params={"limit": 5}, headers=headers()
        ).json()
        assert [i["read"] for i in ascending] == [False, True, False]
        admin = client.get(
            feed_url, params={"limit": 5}, headers=headers("root", admin=True)
        ).json()
        assert all(not i["read"] for i in admin["items"])

    async def test_owner_visibility_identity_and_missing_item(self, env):
        client, service, store, sessions = env
        await _seed(service, sessions)
        nid = client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"][0]["id"]
        url = f"{PREFIX}/notifications/{nid}/read"
        assert client.put(url).status_code == 401
        assert client.put(url, headers=headers("owner-b")).status_code == 404
        assert (
            client.put(f"{PREFIX}/notifications/{uuid4()}/read", headers=headers()).status_code
            == 404
        )
        assert (
            client.put(f"{PREFIX}/notifications/not-a-uuid/read", headers=headers()).status_code
            == 422
        )
        assert not store.reads


def _delivery_headers() -> dict[str, str]:
    """The push gateway's forge:notifications:deliver credential, for its mapped owner."""
    import jwt

    token = jwt.encode(
        {
            "sub": "forge-gateway",
            "token_use": "notification_delivery",
            "scopes": ["forge:notifications:deliver"],
        },
        "test-only-signing-key-32-bytes-long!",
        algorithm="HS256",
    )
    return {**headers("forge-gateway"), "authorization": f"Bearer {token}"}


class TestDeliveryView:
    """The push gateway reads every owner's notifications, each read as its owner reads it."""

    async def test_the_delivery_credential_reads_every_owner_and_org(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions, owner="owner-a", count=2)
        await _seed(service, sessions, owner="owner-b", count=1)
        mine = client.get(f"{PREFIX}/notifications", headers=headers("owner-b")).json()
        read = mine["items"][0]["id"]
        client.put(f"{PREFIX}/notifications/{read}/read", headers=headers("owner-b"))

        page = client.get(
            f"{PREFIX}/notifications", params={"limit": 5}, headers=_delivery_headers()
        ).json()

        assert sorted((item["owner_id"], item["read"]) for item in page["items"]) == [
            ("owner-a", False),
            ("owner-a", False),
            ("owner-b", True),
        ]
        assert page["delivery"] == {
            "owner_unread_counts": {"owner-a": 2, "owner-b": 0},
            "node_pushes_alerts": False,
        }
        assert all(item["tenant_id"] == "tenant-a" for item in page["items"])

    async def test_one_owners_page(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions, owner="owner-a", count=2)
        await _seed(service, sessions, owner="owner-b", count=1)

        page = client.get(
            f"{PREFIX}/notifications",
            params={"owner_id": "owner-b", "view": "delivery"},
            headers=_delivery_headers(),
        ).json()

        assert [item["owner_id"] for item in page["items"]] == ["owner-b"]
        assert page["delivery"]["owner_unread_counts"] == {"owner-b": 1}

    async def test_a_person_cannot_ask_for_the_delivery_view(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions, owner="owner-b", count=1)

        refused = client.get(
            f"{PREFIX}/notifications", params={"view": "delivery"}, headers=headers(admin=True)
        )

        assert refused.status_code == 403
        assert "forge:notifications:deliver" in refused.json()["detail"]

    async def test_owner_id_filters_only_the_delivery_view(self, env):
        client, *_ = env

        refused = client.get(
            f"{PREFIX}/notifications", params={"owner_id": "owner-b"}, headers=headers()
        )

        assert refused.status_code == 422

    async def test_the_view_has_no_single_readers_unread_filter(self, env):
        client, *_ = env

        refused = client.get(
            f"{PREFIX}/notifications", params={"unread": "true"}, headers=_delivery_headers()
        )

        assert refused.status_code == 422

    async def test_a_reader_view_is_unchanged_and_carries_no_delivery_block(self, env):
        client, service, _, sessions = env
        await _seed(service, sessions, owner="owner-b", count=1)

        page = client.get(f"{PREFIX}/notifications", headers=headers()).json()

        assert page["items"] == [] and page["delivery"] is None

    async def test_an_allow_all_host_serves_it_without_a_credential_and_says_it_pushes(self):
        from unittest.mock import AsyncMock

        from identity.adapters.identity import AllowAllIdentityAdapter

        store = InMemoryNotificationStore()
        sessions = InMemorySessionRepository()
        service = NotificationService(
            store.feed,
            store.rule_repo,
            store.outbox,
            image_repository=store.images,
            image_scheduler=store.image_scheduler,
            reply_ready_enabled=True,
            reply_title_chars=80,
            reply_body_chars=100,
            sinks=[],
        )
        app = FastAPI()
        app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
        app.include_router(
            create_notifications_router(
                service,
                SessionService(sessions, MockPodManager(), authorization=OwnerOnly()),
                prefix=PREFIX,
                default_page_size=2,
                max_page_size=5,
                image_cache_max_age_seconds=CACHE_MAX_AGE,
                node_pushes_alerts=True,
            )
        )
        await _seed(service, sessions, owner="someone", count=1)

        page = TestClient(app).get(f"{PREFIX}/notifications", params={"view": "delivery"}).json()

        assert [item["owner_id"] for item in page["items"]] == ["someone"]
        assert page["delivery"]["node_pushes_alerts"] is True
