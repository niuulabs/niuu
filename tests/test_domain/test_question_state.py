"""Question state (LEX-1932): one answer per question, shared by every device."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft
from tests.support.notifications import InMemoryNotificationStore, InMemoryQuestionRepository
from volundr.domain.models import Session
from volundr.domain.notifications import NotificationNotFoundError, NotificationSinkInfo
from volundr.domain.questions import (
    AnsweredBy,
    NotAQuestionError,
    QuestionAnsweredError,
    QuestionClosedError,
    QuestionState,
)
from volundr.domain.services.notifications import (
    NotificationService,
    QuestionStoreUnavailableError,
)

OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
OTHER = Principal(user_id="owner-b", email="", tenant_id="tenant-a", roles=["volundr:developer"])
AGENT = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=[])
MAC = AnsweredBy(device_id="install-mac", device_label="Mac")
PHONE = AnsweredBy(device_id="install-phone", device_label="iPhone")
ASK = {
    "items": [
        {
            "id": "slots",
            "kind": "question",
            "text": "Which slot first?",
            "options": [{"label": "Release", "recommended": True}, {"label": "QA"}],
            "default": {"option": "Release", "after": "PT4H"},
        }
    ]
}


def _session(**overrides) -> Session:
    values = {
        "name": "ios-coordinator",
        "model": "claude-opus-5",
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "session_definition": "skuldClaude",
    }
    values.update(overrides)
    return Session(**values)


def _service(*, questions=True, broadcaster=None):
    store = InMemoryNotificationStore()
    repository = InMemoryQuestionRepository() if questions else None
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        questions=repository,
        question_host="mini-m6" if questions else "",
    )
    return service, store, repository


async def _ask(service, session, *, key="ask-1"):
    notification, _ = await service.submit(
        session,
        AGENT,
        NotificationDraft(kind="decision", title="Which slot first?", question=True, ask=ASK),
        idempotency_key=key,
    )
    return notification


async def _notice(service, session, ref, action, *, key):
    notification, _ = await service.submit(
        session,
        AGENT,
        NotificationDraft(
            kind="info", title=f"Question {action}", about_question={"id": ref, "action": action}
        ),
        idempotency_key=key,
    )
    return notification


async def _answer(service, notification, *, request_id="answer-1", device=PHONE, content="Release"):
    return await service.answer_question(
        OWNER,
        notification.id,
        request_id=request_id,
        device=device,
        content=content,
        answers=[{"item_id": "slots", "selected": [content]}],
    )


class TestAnswer:
    async def test_the_first_answer_is_recorded_and_resolves_the_question(self):
        service, store, questions = _service()
        session = _session()
        question = await _ask(service, session)

        notification, record, recorded = await _answer(service, question)

        assert recorded is True
        assert notification.id == question.id
        assert record.state is QuestionState.ANSWERED
        assert record.answer.device == PHONE
        assert record.answer.request_id == "answer-1"
        assert record.answer.content == "Release"
        assert record.answer.answers == [{"item_id": "slots", "selected": ["Release"]}]
        stored = await store.feed.get(question.id)
        assert stored.resolved
        assert stored.resolution == "Answered on iPhone"
        assert [(e.seq, e.state, e.answered_by) for e in questions.events] == [
            (1, QuestionState.ANSWERED, PHONE)
        ]
        wire = record.wire(service.question_ref(question.id))
        assert wire["ref"] == f"question:mini-m6:{question.id}"
        assert wire["answered_by"] == {"device_id": "install-phone", "device_label": "iPhone"}
        assert wire["answer"]["content"] == "Release"

    async def test_the_same_request_again_is_the_same_answer(self):
        service, _, questions = _service()
        question = await _ask(service, _session())
        await _answer(service, question)

        _, record, recorded = await _answer(service, question)

        assert recorded is False
        assert record.answer.device == PHONE
        assert len(questions.events) == 1

    async def test_a_second_answer_is_refused_with_the_first(self):
        service, _, questions = _service()
        question = await _ask(service, _session())
        await _answer(service, question, device=MAC, content="QA")

        with pytest.raises(QuestionAnsweredError) as refused:
            await _answer(service, question, request_id="answer-2", device=PHONE)

        first = refused.value.record
        assert first.answer.device == MAC
        assert first.answer.content == "QA"
        assert len(questions.events) == 1

    async def test_a_withdrawn_question_takes_no_answer(self):
        service, _, _ = _service()
        session = _session()
        question = await _ask(service, session)
        await _notice(
            service, session, service.question_ref(question.id), "withdrawn", key="withdraw"
        )

        with pytest.raises(QuestionClosedError) as refused:
            await _answer(service, question)

        assert refused.value.record.state is QuestionState.WITHDRAWN

    async def test_a_question_resolved_by_hand_takes_no_answer(self):
        """An older app build resolves "Answered on …" through PUT /resolve."""
        service, _, questions = _service()
        question = await _ask(service, _session())
        await service.resolve(OWNER, question.id, resolution="Answered on iPad")

        with pytest.raises(QuestionClosedError) as refused:
            await _answer(service, question)

        assert refused.value.record is None
        assert refused.value.resolution == "Answered on iPad"
        assert questions.events == []

    async def test_only_a_question_is_answered(self):
        service, _, _ = _service()
        notice, _ = await service.submit(
            _session(),
            AGENT,
            NotificationDraft(kind="milestone", title="Done"),
            idempotency_key="m",
        )
        with pytest.raises(NotAQuestionError):
            await _answer(service, notice)

    async def test_a_runtime_question_is_answered_on_the_session_socket(self):
        service, _, _ = _service()
        attention = await service.record_attention(
            _session(),
            state_since=datetime.now(UTC),
            kind="question",
            prompt="Pick one",
            request_id="req-1",
        )
        with pytest.raises(NotAQuestionError, match="session socket"):
            await _answer(service, attention)

    async def test_another_owners_question_is_not_found(self):
        service, _, _ = _service()
        question = await _ask(service, _session())
        with pytest.raises(NotificationNotFoundError):
            await service.answer_question(
                OTHER,
                question.id,
                request_id="a",
                device=PHONE,
                content="x",
                answers=None,
            )
        with pytest.raises(NotificationNotFoundError):
            await service.answer_question(
                OWNER, uuid4(), request_id="a", device=PHONE, content="x", answers=None
            )

    async def test_without_question_state_the_answer_is_refused_loudly(self):
        service, _, _ = _service(questions=False)
        question = await _ask(service, _session())
        with pytest.raises(QuestionStoreUnavailableError, match="000123"):
            await _answer(service, question)

    def test_question_state_needs_a_host_key(self):
        store = InMemoryNotificationStore()
        with pytest.raises(ValueError, match="host key"):
            NotificationService(
                store.feed,
                store.rule_repo,
                store.outbox,
                image_repository=store.images,
                image_scheduler=store.image_scheduler,
                reply_ready_enabled=False,
                reply_title_chars=40,
                reply_body_chars=60,
                sinks=[],
                questions=InMemoryQuestionRepository(),
            )


class TestClose:
    async def test_the_asking_session_resolves_its_answered_question(self):
        service, store, questions = _service()
        session = _session()
        question = await _ask(service, session)
        await _answer(service, question)

        notice = await _notice(
            service, session, service.question_ref(question.id), "resolved", key="done"
        )

        record = await questions.get(question.id)
        assert record.state is QuestionState.RESOLVED
        assert record.closed_by_notification_id == notice.id
        assert record.answer.device == PHONE
        assert [e.state for e in questions.events] == [
            QuestionState.ANSWERED,
            QuestionState.RESOLVED,
        ]
        # Already resolved by the answer: the resolution text stays.
        assert (await store.feed.get(question.id)).resolution == "Answered on iPhone"

    async def test_withdrawing_an_open_question_resolves_its_notice(self):
        service, store, questions = _service()
        session = _session()
        question = await _ask(service, session)

        notice = await _notice(
            service, session, service.question_ref(question.id), "withdrawn", key="w"
        )

        stored = await store.feed.get(question.id)
        assert stored.resolved
        assert stored.resolved_by_notification_id == notice.id
        assert [e.state for e in questions.events] == [QuestionState.WITHDRAWN]

    async def test_a_second_close_changes_nothing(self):
        service, _, questions = _service()
        session = _session()
        question = await _ask(service, session)
        ref = service.question_ref(question.id)
        await _notice(service, session, ref, "withdrawn", key="w1")
        await _notice(service, session, ref, "resolved", key="w2")

        assert (await questions.get(question.id)).state is QuestionState.WITHDRAWN
        assert len(questions.events) == 1

    async def test_another_session_cannot_close_it(self, caplog):
        service, store, questions = _service()
        question = await _ask(service, _session())
        other = _session(name="worker")

        await _notice(service, other, service.question_ref(question.id), "resolved", key="x")

        assert await questions.get(question.id) is None
        assert not (await store.feed.get(question.id)).resolved
        assert "only the asking session may close its question" in caplog.text

    async def test_a_relay_or_remind_does_not_close_it(self):
        service, _, questions = _service()
        session = _session()
        question = await _ask(service, session)
        ref = service.question_ref(question.id)
        await _notice(service, session, ref, "remind", key="r1")
        await _notice(service, _session(name="coordinator"), ref, "relay", key="r2")

        assert await questions.get(question.id) is None

    async def test_another_hosts_question_is_left_to_that_host(self):
        service, _, questions = _service()
        session = _session()
        question = await _ask(service, session)

        await _notice(service, session, f"question:thor:{question.id}", "resolved", key="t")

        assert await questions.get(question.id) is None


class TestRuntimeQuestions:
    async def _attention(self, service, session, request_id):
        return await service.record_attention(
            session,
            state_since=datetime.now(UTC),
            kind="question",
            prompt="Pick one",
            request_id=request_id,
        )

    async def test_a_session_that_stops_waiting_settles_its_runtime_questions(self):
        """Forge cannot tell an answer from an interrupt: settled, never "answered"."""
        service, store, questions = _service()
        session = _session()
        attention = await self._attention(service, session, "req-1")

        await service.retire_attention(session)
        await service.retire_attention(session)

        record = await questions.get(attention.id)
        assert record.state is QuestionState.RESOLVED
        assert record.answer is None
        stored = await store.feed.get(attention.id)
        assert stored.resolution == "No longer waiting in the session"
        assert [(e.state, e.answered_by) for e in questions.events] == [
            (QuestionState.RESOLVED, None)
        ]

    async def test_a_read_runtime_question_is_settled_too(self):
        service, store, questions = _service()
        session = _session()
        first = await self._attention(service, session, "req-1")
        second = await self._attention(service, session, "req-2")
        await store.feed.advance_watermark(
            "owner-a", read_through_seq=second.seq, expected_revision=0
        )

        await service.retire_attention(session)

        assert {(await questions.get(n.id)).state for n in (first, second)} == {
            QuestionState.RESOLVED
        }

    async def test_a_notice_question_is_not_settled_with_the_session(self):
        service, _, questions = _service()
        session = _session()
        question = await _ask(service, session)

        await service.retire_attention(session)

        assert await questions.get(question.id) is None


class TestEvents:
    async def test_events_are_the_readers_own_in_order(self):
        service, _, _ = _service()
        session = _session()
        first, second = await _ask(service, session, key="a"), await _ask(service, session, key="b")
        await _answer(service, first)
        await _notice(service, session, service.question_ref(second.id), "withdrawn", key="w")

        items, head = await service.question_events(OWNER, after=0, limit=10)

        assert head == 2
        assert [(i["seq"], i["notification_id"], i["state"]) for i in items] == [
            (1, str(first.id), "answered"),
            (2, str(second.id), "withdrawn"),
        ]
        assert items[0]["ref"] == f"question:mini-m6:{first.id}"
        assert items[0]["answered_by"] == {"device_id": "install-phone", "device_label": "iPhone"}
        assert items[1]["answered_by"] is None
        later, _ = await service.question_events(OWNER, after=1, limit=10)
        assert [i["seq"] for i in later] == [2]
        assert await service.question_events(OTHER, after=0, limit=10) == ([], 0)

    async def test_without_question_state_events_are_refused(self):
        service, _, _ = _service(questions=False)
        with pytest.raises(QuestionStoreUnavailableError):
            await service.question_events(OWNER, after=0, limit=10)


class TestQuestionRef:
    async def test_a_question_and_a_notice_about_it_share_the_ref(self):
        service, _, _ = _service()
        session = _session()
        question = await _ask(service, session)
        ref = service.question_ref(question.id)
        remind = await _notice(service, session, ref, "remind", key="r")
        plain, _ = await service.submit(
            session, AGENT, NotificationDraft(kind="info", title="FYI"), idempotency_key="p"
        )

        assert question.question_ref("mini-m6") == ref
        assert remind.question_ref("mini-m6") == ref
        assert plain.question_ref("mini-m6") is None


class TestReviewGaps:
    async def test_the_same_request_from_another_device_is_a_second_answer(self):
        service, _, _ = _service()
        question = await _ask(service, _session())
        await _answer(service, question)
        with pytest.raises(QuestionAnsweredError):
            await _answer(service, question, device=MAC)
        with pytest.raises(QuestionAnsweredError):
            await _answer(service, question, content="QA")

    async def test_nothing_is_recorded_when_the_precheck_refuses(self):
        service, store, questions = _service()
        question = await _ask(service, _session())

        async def refuse(_notification):
            raise RuntimeError("asking session is not running")

        with pytest.raises(RuntimeError):
            await service.answer_question(
                OWNER,
                question.id,
                request_id="a",
                device=PHONE,
                content="x",
                answers=None,
                before_claim=refuse,
            )

        assert questions.events == []
        assert not (await store.feed.get(question.id)).resolved

    async def test_closing_a_notice_that_is_not_a_question_does_nothing(self):
        service, store, questions = _service()
        session = _session()
        plain, _ = await service.submit(
            session, AGENT, NotificationDraft(kind="info", title="FYI"), idempotency_key="p"
        )

        await _notice(service, session, service.question_ref(plain.id), "resolved", key="r")

        assert questions.events == []
        assert not (await store.feed.get(plain.id)).resolved

    def test_a_host_key_too_long_for_a_collapse_id_is_fatal(self):
        store = InMemoryNotificationStore()
        with pytest.raises(ValueError, match="shorten host_identity.host"):
            NotificationService(
                store.feed,
                store.rule_repo,
                store.outbox,
                image_repository=store.images,
                image_scheduler=store.image_scheduler,
                reply_ready_enabled=False,
                reply_title_chars=40,
                reply_body_chars=60,
                sinks=[],
                questions=InMemoryQuestionRepository(),
                question_host="thor.tail737f2a.ts.net",
            )
