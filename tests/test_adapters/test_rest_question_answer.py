"""POST /api/v1/forge/notifications/{id}/answer (LEX-1932): one answer per question."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    make_session_participant_service,
)
from tests.test_domain.test_question_state import ASK
from tests.test_domain.test_question_state import _service as question_service
from volundr.adapters.inbound.rest import create_router
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.services import SessionService, StatsService

USER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
AGENT = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=[])


class _Socket:
    """The asking session's broker: records frames, ACKs each one as delivered."""

    def __init__(self, ack: str | None = "user_delivered") -> None:
        self.sent: list[dict] = []
        self.ack = ack
        self._acked = 0

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if self.ack is None or self._acked >= len(self.sent):
            raise TimeoutError
        self._acked += 1
        return json.dumps({"type": self.ack, "request_id": self.sent[-1]["request_id"]})


class _Connect:
    def __init__(self, socket: _Socket) -> None:
        self.socket = socket
        self.calls = 0

    def __call__(self, url: str, **kwargs: object):
        self.calls += 1
        socket = self.socket

        class _Ctx:
            async def __aenter__(self) -> _Socket:
                return socket

            async def __aexit__(self, *exc) -> bool:
                return False

        return _Ctx()


@pytest.fixture
def world(repository: InMemorySessionRepository, pod_manager):
    sessions = SessionService(repository, pod_manager)
    stats = StatsService(
        InMemoryStatsRepository(
            active_sessions=0,
            total_sessions=0,
            tokens_today=0,
            local_tokens=0,
            cloud_tokens=0,
            cost_today=Decimal("0"),
        ),
        sessions,
    )
    app = FastAPI()
    app.include_router(
        create_router(
            sessions,
            stats,
            pricing_provider=InMemoryPricingProvider(),
            session_participant_service=make_session_participant_service(sessions),
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    notifications, store, questions = question_service()
    app.state.notification_service = notifications
    session = Session(
        id=uuid4(),
        name="ios-coordinator",
        model="claude-opus-5",
        owner_id="owner-a",
        tenant_id="tenant-a",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        status=SessionStatus.RUNNING,
        chat_endpoint="ws://localhost:8080/s/ios-coordinator/session",
    )
    asyncio.run(repository.create(session))
    question, _ = asyncio.run(
        notifications.submit(
            session,
            AGENT,
            NotificationDraft(kind="decision", title="Which slot first?", question=True, ask=ASK),
            idempotency_key="ask",
        )
    )
    return TestClient(app), notifications, store, questions, question


def _answer(client, question, socket, *, principal=USER, **body):
    payload = {
        "request_id": "answer-1",
        "device_id": "install-phone",
        "device_label": "iPhone",
        "content": "Release",
        "answers": [{"item_id": "slots", "selected": ["Release"]}],
        **body,
    }
    connect = _Connect(socket)
    with (
        patch("websockets.asyncio.client.connect", new=connect),
        patch(
            "volundr.adapters.inbound.rest.extract_principal",
            new=AsyncMock(return_value=principal),
        ),
    ):
        response = client.post(f"/api/v1/forge/notifications/{question.id}/answer", json=payload)
    return response, connect


def test_the_first_answer_is_delivered_once_and_resolves_the_question(world):
    client, notifications, store, _, question = world
    socket = _Socket()

    response, _ = _answer(client, question, socket)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "delivered"
    assert body["question"]["ref"] == f"question:mini-m6:{question.id}"
    assert body["question"]["state"] == "answered"
    assert body["question"]["answered_by"] == {
        "device_id": "install-phone",
        "device_label": "iPhone",
    }
    assert [(f["content"], f["request_id"]) for f in socket.sent] == [("Release", "answer-1")]
    assert socket.sent[0]["sender"]["kind"] == "user"
    stored = asyncio.run(store.feed.get(question.id))
    assert stored.resolution == "Answered on iPhone"


def test_a_second_device_is_refused_with_the_first_answer_and_nothing_is_sent(world):
    client, _, _, _, question = world
    _answer(client, question, _Socket(), device_id="install-mac", device_label="Mac")
    socket = _Socket()

    response, connect = _answer(
        client, question, socket, request_id="answer-2", content="QA", device_label="iPad"
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "question_answered"
    assert detail["question"]["answered_by"] == {"device_id": "install-mac", "device_label": "Mac"}
    assert detail["question"]["answer"]["content"] == "Release"
    assert connect.calls == 0
    assert socket.sent == []


def test_a_retry_of_the_same_answer_is_delivered_again(world):
    client, _, _, questions, question = world
    _answer(client, question, _Socket(ack=None))
    socket = _Socket()

    response, _ = _answer(client, question, socket)

    assert response.status_code == 200
    assert [f["request_id"] for f in socket.sent] == ["answer-1"]
    assert len(questions.events) == 1


def test_a_withdrawn_question_answers_question_closed(world):
    client, notifications, _, _, question = world
    session_id = question.session_id
    session = Session(
        id=session_id,
        name="ios-coordinator",
        model="claude-opus-5",
        owner_id="owner-a",
        tenant_id="tenant-a",
    )
    asyncio.run(
        notifications.submit(
            session,
            AGENT,
            NotificationDraft(
                kind="info",
                title="No longer needed",
                about_question={
                    "id": notifications.question_ref(question.id),
                    "action": "withdrawn",
                },
            ),
            idempotency_key="withdraw",
        )
    )

    response, connect = _answer(client, question, _Socket())

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "question_closed"
    assert response.json()["detail"]["question"]["state"] == "withdrawn"
    assert connect.calls == 0


def test_an_undelivered_answer_stays_recorded_and_says_to_retry(world):
    client, _, _, questions, question = world
    socket = _Socket(ack="user_delivery_failed")

    response, _ = _answer(client, question, socket)

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "answer_not_delivered"
    assert "retry with the same request_id" in detail["message"]
    assert detail["question"]["state"] == "answered"
    assert len(questions.events) == 1


def test_a_session_credential_cannot_answer(world):
    client, _, _, _, question = world
    session_principal = Principal(
        user_id="owner-a",
        email="",
        tenant_id="tenant-a",
        roles=[],
        token_use="forge_session",
        bound_session_id=str(uuid4()),
    )
    response, _ = _answer(client, question, _Socket(), principal=session_principal)
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"device_label": " "}, "device_label"),
        ({"device_id": ""}, "device_id"),
        ({"device_id": "x" * 65}, "device_id"),
        ({"content": "  "}, "content"),
        ({"request_id": "bad id"}, "request_id"),
        ({"unknown": 1}, "unknown"),
    ],
)
def test_a_malformed_answer_is_refused(world, body, field):
    client, _, _, questions, question = world
    response, _ = _answer(client, question, _Socket(), **body)
    assert response.status_code == 422
    assert field in json.dumps(response.json())
    assert questions.events == []


def test_answering_a_non_question_is_422(world):
    client, notifications, _, _, question = world
    session = Session(
        id=question.session_id,
        name="ios-coordinator",
        model="claude-opus-5",
        owner_id="owner-a",
        tenant_id="tenant-a",
    )
    plain, _ = asyncio.run(
        notifications.submit(
            session, AGENT, NotificationDraft(kind="milestone", title="Done"), idempotency_key="m"
        )
    )
    response, _ = _answer(client, plain, _Socket())
    assert response.status_code == 422
    assert "not a question" in response.json()["detail"]


def test_an_unknown_question_is_404(world):
    client, _, _, _, question = world
    response, _ = _answer(
        client, type(question)(**{**question.model_dump(), "id": uuid4()}), _Socket()
    )
    assert response.status_code == 404


def test_a_tenant_admin_cannot_answer_for_the_owner(world):
    client, _, _, questions, question = world
    admin = Principal(user_id="root", email="", tenant_id="tenant-a", roles=["volundr:admin"])

    response, connect = _answer(client, question, _Socket(), principal=admin)

    assert response.status_code == 404
    assert connect.calls == 0
    assert questions.events == []


def test_an_asking_session_that_is_not_running_records_nothing(world, repository):
    client, _, store, questions, question = world
    session = asyncio.run(repository.get(question.session_id))
    asyncio.run(repository.update(session.model_copy(update={"chat_endpoint": None})))

    response, connect = _answer(client, question, _Socket())

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]
    assert connect.calls == 0
    assert questions.events == []
    assert not asyncio.run(store.feed.get(question.id)).resolved
    # Once it runs, the same device answers.
    asyncio.run(repository.update(session))
    assert _answer(client, question, _Socket())[0].status_code == 200


def test_a_retry_delivers_the_recorded_answer_and_other_text_is_refused(world):
    client, _, _, _, question = world
    _answer(client, question, _Socket(ack=None))

    changed, connect = _answer(client, question, _Socket(), content="QA")
    other_device, _ = _answer(client, question, _Socket(), device_id="install-mac")
    socket = _Socket()
    retry, _ = _answer(client, question, socket)

    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "question_answered"
    assert connect.calls == 0
    assert other_device.status_code == 409
    assert retry.status_code == 200
    assert [f["content"] for f in socket.sent] == ["Release"]
