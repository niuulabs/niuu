"""The node's feed for LEX-1932: a batch read by id, question refs and question events."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.notifications import InMemoryNotificationStore, InMemoryQuestionRepository
from tests.test_adapters.test_rest_notifications import OwnerOnly, headers
from tests.test_domain.test_question_state import ASK, PHONE
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.domain.models import Session
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
AGENT = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=[])
OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])


def _app(*, questions: bool = True):
    store = InMemoryNotificationStore()
    sessions = InMemorySessionRepository()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        reply_ready_enabled=False,
        reply_title_chars=80,
        reply_body_chars=100,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        questions=InMemoryQuestionRepository() if questions else None,
        question_host="thor" if questions else "",
    )
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(
        create_notifications_router(
            service,
            SessionService(sessions, MockPodManager(), authorization=OwnerOnly()),
            prefix=PREFIX,
            default_page_size=2,
            max_page_size=5,
            image_cache_max_age_seconds=3600,
        )
    )
    return TestClient(app), service


@pytest.fixture
def env():
    return _app()


async def _submit(service, session, key, **draft):
    notification, _ = await service.submit(
        session, AGENT, NotificationDraft(**draft), idempotency_key=key
    )
    return notification


SESSION = Session(name="asker", owner_id="owner-a", tenant_id="tenant-a")


class TestBatchRead:
    async def test_ids_return_only_those_items_whatever_their_age(self, env):
        client, service = env
        items = [
            await _submit(service, SESSION, f"k{i}", kind="info", title=f"t{i}") for i in range(4)
        ]
        other = await _submit(
            service,
            Session(name="b", owner_id="owner-b", tenant_id="tenant-a"),
            "b",
            kind="info",
            title="b",
        )

        wanted = f"{items[0].id}, {items[2].id},{other.id},{uuid4()}"
        page = client.get(
            f"{PREFIX}/notifications", params={"ids": wanted, "limit": 5}, headers=headers()
        ).json()

        assert {item["id"] for item in page["items"]} == {str(items[0].id), str(items[2].id)}

    def test_more_ids_than_the_page_is_refused(self, env):
        client, _ = env
        ids = ",".join(str(uuid4()) for _ in range(3))
        response = client.get(
            f"{PREFIX}/notifications", params={"ids": ids, "limit": 2}, headers=headers()
        )
        assert response.status_code == 422
        assert "raise limit" in response.json()["detail"]

    def test_a_malformed_id_is_refused(self, env):
        client, _ = env
        response = client.get(f"{PREFIX}/notifications", params={"ids": "nope"}, headers=headers())
        assert response.status_code == 422
        assert "nope" in response.json()["detail"]


class TestQuestionRef:
    async def test_a_question_and_a_notice_about_it_carry_its_ref(self, env):
        client, service = env
        question = await _submit(
            service, SESSION, "q", kind="decision", title="Pick?", question=True, ask=ASK
        )
        ref = f"question:thor:{question.id}"
        await _submit(
            service,
            SESSION,
            "r",
            kind="info",
            title="Still need this",
            about_question={"id": ref, "action": "remind"},
        )
        await _submit(service, SESSION, "p", kind="info", title="FYI")

        page = client.get(f"{PREFIX}/notifications", params={"limit": 5}, headers=headers()).json()

        assert [(item["title"], item["question_ref"]) for item in page["items"]] == [
            ("FYI", None),
            ("Still need this", ref),
            ("Pick?", ref),
        ]

    async def test_without_question_state_no_ref_is_served(self):
        client, service = _app(questions=False)
        await _submit(service, SESSION, "q", kind="decision", title="Pick?", question=True, ask=ASK)
        page = client.get(f"{PREFIX}/notifications", headers=headers()).json()
        assert page["items"][0]["question_ref"] is None


class TestQuestionEvents:
    async def test_changes_page_oldest_first_with_the_head(self, env):
        client, service = env
        first = await _submit(service, SESSION, "a", kind="decision", title="A?", question=True)
        second = await _submit(service, SESSION, "b", kind="decision", title="B?", question=True)
        await service.answer_question(
            OWNER, first.id, request_id="r1", device=PHONE, content="yes", answers=None
        )
        await _submit(
            service,
            SESSION,
            "w",
            kind="info",
            title="Never mind",
            about_question={"id": f"question:thor:{second.id}", "action": "withdrawn"},
        )

        page = client.get(
            f"{PREFIX}/notifications/questions/events", params={"limit": 1}, headers=headers()
        ).json()
        assert page["head_seq"] == 2
        assert page["next_after"] == 1
        assert page["items"] == [
            {
                "seq": 1,
                "ref": f"question:thor:{first.id}",
                "notification_id": str(first.id),
                "owner_id": "owner-a",
                "state": "answered",
                "answered_by": {"device_id": "install-phone", "device_label": "iPhone"},
                "at": page["items"][0]["at"],
            }
        ]
        rest = client.get(
            f"{PREFIX}/notifications/questions/events",
            params={"after": page["next_after"]},
            headers=headers(),
        ).json()
        assert [(i["seq"], i["state"]) for i in rest["items"]] == [(2, "withdrawn")]
        nothing = client.get(
            f"{PREFIX}/notifications/questions/events", params={"after": 2}, headers=headers()
        ).json()
        assert (nothing["items"], nothing["next_after"]) == ([], 2)
        others = client.get(
            f"{PREFIX}/notifications/questions/events", headers=headers("owner-b")
        ).json()
        assert (others["items"], others["head_seq"]) == ([], 0)

    def test_without_question_state_the_route_says_so(self):
        client, _ = _app(questions=False)
        response = client.get(f"{PREFIX}/notifications/questions/events", headers=headers())
        assert response.status_code == 503
        assert "000123" in response.json()["detail"]
