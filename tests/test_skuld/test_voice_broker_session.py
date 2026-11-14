"""The broker as the session a voice call is bound to, and the channel's composition."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from niuu.domain.message_author import MessageAuthor
from skuld.config import SkuldSettings
from skuld.conversation_models import ConversationTurn
from skuld.voice.broker_session import (
    VOICE_CALL_REQUEST_PREFIX,
    BrokerVoiceSession,
    VoiceRecordError,
    build_voice_channel,
)
from skuld.voice.channel import SessionVoiceChannel
from skuld.voice.persona import VoicePersonaError
from skuld.voice.session_port import SendStatus
from tests.test_skuld.voice_support import OWNER, persona_document

AUTHOR = MessageAuthor(user_id=OWNER, display_name="Ada")
RUNNING_STATES = {"active", "tool_executing"}


class FakeBroker:
    """The attributes of the real broker that ``BrokerVoiceSession`` reads."""

    def __init__(self) -> None:
        self.session_id = "sess-1"
        self.model = "claude-sonnet"
        self._settings = SimpleNamespace(
            session=SimpleNamespace(name="Refactor billing"), cli_type="claude"
        )
        self._activity_state = "idle"
        self._activity_state_since: float | None = 1234.5
        self._pending_ask_user_questions: dict[str, dict[str, Any]] = {}
        self._conversation_turns: list[ConversationTurn] = []
        self.submitted: list[dict[str, Any]] = []
        #: What ``_submit_user_turn`` does: raise this, or hand back a task running ``delivery``.
        self.submit_error: Exception | None = None
        self.delivery = self._delivered
        #: The steering state the accepted turn ends up in, when it is recorded at all.
        self.steering_state: str | None = None
        self.enqueued: list[dict[str, Any]] = []
        self.enqueue_result: dict[str, Any] | None = {"seq": 1}
        self.tasks: list[asyncio.Task] = []

    def _coarse_bucket(self, state: str) -> str:
        return "running" if state in RUNNING_STATES else state

    async def _delivered(self) -> None:
        return None

    async def _submit_user_turn(self, text: str, **kwargs: Any) -> asyncio.Task:
        self.submitted.append({"text": text, **kwargs})
        if self.submit_error is not None:
            raise self.submit_error
        if self.steering_state is not None:
            self._conversation_turns.append(
                ConversationTurn(
                    id=kwargs["msg_id"],
                    role="user",
                    content=text,
                    metadata={"steering_state": self.steering_state},
                )
            )
        task = asyncio.create_task(self.delivery())
        self.tasks.append(task)
        return task

    def _enqueue_event_log(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        self.enqueued.append(frame)
        return self.enqueue_result

    async def _get_session_http_client(self):
        raise AssertionError("not reached in these tests")

    async def finish(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


@pytest.fixture
async def broker():
    fake = FakeBroker()
    yield fake
    await fake.finish()


def _session(broker: FakeBroker, *, confirm: float = 1.0) -> BrokerVoiceSession:
    return BrokerVoiceSession(broker, send_confirm_seconds=confirm)


class TestStatus:
    def test_idle(self, broker):
        status = _session(broker).status()

        assert status.session_id == "sess-1"
        assert status.name == "Refactor billing"
        assert status.model == "claude-sonnet"
        assert status.harness == "claude"
        assert status.activity_state == "idle"
        assert status.busy is False
        assert status.state_since == 1234.5
        assert status.questions == ()

    @pytest.mark.parametrize(
        ("state", "busy"),
        [("active", True), ("tool_executing", True), ("awaiting_input", False), ("idle", False)],
    )
    def test_busy_is_the_running_bucket(self, broker, state, busy):
        broker._activity_state = state

        status = _session(broker).status()

        assert status.activity_state == state
        assert status.busy is busy

    def test_questions_come_from_pending_asks_and_skip_what_is_not_a_question(self, broker):
        broker._pending_ask_user_questions = {
            "a": {"questions": [{"question": " Which db? "}, {"question": "  "}, "junk", {}]},
            "b": {"questions": "not a list"},
            "c": {"questions": [{"question": "Which region?"}]},
            "d": {},
        }

        assert _session(broker).status().questions == ("Which db?", "Which region?")


class TestTurns:
    def test_only_public_non_empty_user_and_assistant_turns_in_order(self, broker):
        broker._conversation_turns = [
            ConversationTurn(
                id="1",
                role="user",
                content="hello",
                author={"user_id": "u1", "display_name": "Ada"},
            ),
            ConversationTurn(id="2", role="assistant", content="hi"),
            ConversationTurn(id="3", role="system", content="boot"),
            ConversationTurn(id="4", role="tool", content="result"),
            ConversationTurn(id="5", role="assistant", content="private", visibility="internal"),
            ConversationTurn(id="6", role="user", content="   "),
            ConversationTurn(id="7", role="user", content=""),
            ConversationTurn(
                id="8", role="user", content="no name", author={"user_id": "u2", "display_name": ""}
            ),
            ConversationTurn(id="9", role="user", content="malformed author", author={"x": 1}),
        ]

        turns = _session(broker).turns()

        assert [(t.role, t.text, t.author) for t in turns] == [
            ("user", "hello", "Ada"),
            ("assistant", "hi", ""),
            ("user", "no name", "u2"),
            ("user", "malformed author", ""),
        ]
        assert turns[0].created_at == broker._conversation_turns[0].created_at


class TestSend:
    async def test_delivered_is_sent_as_the_person_with_the_call_id(self, broker):
        broker._pending_ask_user_questions = {"a": {}, "b": {}}

        outcome = await _session(broker).send("run the tests", author=AUTHOR, call_id="C1")

        assert outcome.status is SendStatus.DELIVERED
        assert outcome.pending_questions == 2
        submitted = broker.submitted[0]
        assert submitted["text"] == "run the tests"
        assert submitted["source"] == "voice"
        assert submitted["author"] == AUTHOR
        assert submitted["extra_metadata"] == {"voice_call_id": "C1"}
        assert submitted["request_id"].startswith(VOICE_CALL_REQUEST_PREFIX)
        assert submitted["msg_id"] and submitted["msg_id"] != submitted["request_id"]

    async def test_no_call_id_sends_no_call_metadata(self, broker):
        await _session(broker).send("go", author=None, call_id="")

        assert broker.submitted[0]["extra_metadata"] is None
        assert broker.submitted[0]["author"] is None

    async def test_each_send_has_its_own_ids(self, broker):
        session = _session(broker)

        await session.send("one", author=AUTHOR, call_id="C")
        await session.send("two", author=AUTHOR, call_id="C")

        first, second = broker.submitted
        assert first["msg_id"] != second["msg_id"]
        assert first["request_id"] != second["request_id"]

    async def test_a_busy_agent_that_does_not_confirm_in_time_is_queued_and_still_delivered(
        self, broker
    ):
        release = asyncio.Event()

        async def slow() -> None:
            await release.wait()

        broker.delivery = slow

        outcome = await _session(broker, confirm=0.01).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.QUEUED
        task = broker.tasks[0]
        assert not task.done() and not task.cancelled()  # the wait timing out must not cancel it
        release.set()
        await task
        assert task.exception() is None

    async def test_a_turn_already_active_on_timeout_counts_as_delivered(self, broker):
        release = asyncio.Event()

        async def slow() -> None:
            await release.wait()

        broker.delivery = slow
        broker.steering_state = "active"
        broker._pending_ask_user_questions = {"a": {}}

        outcome = await _session(broker, confirm=0.01).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.DELIVERED
        assert outcome.pending_questions == 1

    async def test_a_turn_still_pending_on_timeout_is_queued(self, broker):
        release = asyncio.Event()

        async def slow() -> None:
            await release.wait()

        broker.delivery = slow
        broker.steering_state = "pending"

        outcome = await _session(broker, confirm=0.01).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.QUEUED

    async def test_other_turns_are_not_mistaken_for_the_sent_one(self, broker):
        broker._conversation_turns = [
            ConversationTurn(
                id="earlier", role="user", content="x", metadata={"steering_state": "failed"}
            ),
        ]

        outcome = await _session(broker).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.DELIVERED

    async def test_a_failed_steering_state_is_a_failure_with_the_transcript_pointer(self, broker):
        broker.steering_state = "failed"

        outcome = await _session(broker).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.FAILED
        assert "did not take it" in outcome.reason
        assert "transcript shows it as failed" in outcome.reason

    async def test_a_submit_that_raises_is_a_failure_with_the_reason(self, broker, caplog):
        broker.submit_error = RuntimeError("transport is down")

        outcome = await _session(broker).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == "the session did not accept it (transport is down)"
        assert "could not send a message" in caplog.text

    async def test_a_delivery_that_raises_is_a_failure_with_the_reason(self, broker, caplog):
        async def broken() -> None:
            raise RuntimeError("stdin closed")

        broker.delivery = broken

        outcome = await _session(broker).send("go", author=AUTHOR, call_id="C")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == "its delivery failed (stdin closed)"
        assert "delivery raised" in caplog.text


class TestRecord:
    def test_a_frame_goes_to_the_durable_event_log(self, broker):
        frame = {"type": "voice_call", "event": "started"}

        _session(broker).record(frame)

        assert broker.enqueued == [frame]

    def test_a_log_that_is_off_is_an_error_naming_the_remedy(self, broker):
        broker.enqueue_result = None

        with pytest.raises(VoiceRecordError, match="event_log_enabled and volundr_api_url"):
            _session(broker).record({"type": "voice_call"})


class TestBuildVoiceChannel:
    def _settings(self, tmp_path, **voice: Any) -> SkuldSettings:
        return SkuldSettings(
            session={
                "id": "s1",
                "name": "Refactor billing",
                "workspace_dir": str(tmp_path),
                "owner_id": OWNER,
            },
            volundr_api_url="http://forge.test",
            forge_mcp={"token": "forge-session-token"},
            voice={"enabled": True, "default_persona": persona_document(), **voice},
        )

    def _broker(self, settings: SkuldSettings) -> FakeBroker:
        broker = FakeBroker()
        broker._settings = settings
        return broker

    def test_composes_a_channel_for_the_owner(self, tmp_path):
        broker = self._broker(self._settings(tmp_path))

        channel = build_voice_channel(broker)

        assert isinstance(channel, SessionVoiceChannel)
        owner = channel.caller({"x-auth-user-id": OWNER})
        assert owner is not None
        assert channel.caller({"x-auth-user-id": "someone-else"}) is None
        assert channel.health()["tools"] == 3

    def test_the_display_name_and_models_come_from_config(self, tmp_path):
        settings = self._settings(
            tmp_path, display_name="Max", models={"grok": "g-1", "gpt-realtime": "r-1"}
        )

        channel = build_voice_channel(self._broker(settings))

        prompt = channel._composer.prompt(
            "grok", channel._session.status(), [], surface="ios", conversation_id="c"
        )
        assert prompt["displayName"] == "Max"
        assert prompt["session"]["model"] == "g-1"

    def test_the_session_persona_with_a_voice_is_used(self, tmp_path):
        settings = self._settings(tmp_path)
        settings.session.persona = persona_document("alice", voices={"openai-realtime": "coral"})

        channel = build_voice_channel(self._broker(settings))

        assert channel._composer.persona.name == "alice"

    def test_no_voice_persona_is_fatal_with_the_remedy(self, tmp_path):
        settings = self._settings(tmp_path, default_persona=None)

        with pytest.raises(VoicePersonaError, match="voice.default_persona"):
            build_voice_channel(self._broker(settings))

    def test_a_default_persona_without_a_voice_is_fatal(self, tmp_path):
        settings = self._settings(tmp_path, default_persona=persona_document(with_voice=False))

        with pytest.raises(VoicePersonaError, match="no voice: section"):
            build_voice_channel(self._broker(settings))

    def test_the_minter_uses_the_brokers_session_client_and_session_id(self, tmp_path):
        broker = self._broker(self._settings(tmp_path))

        channel = build_voice_channel(broker)

        assert channel._minter._path == "/api/v1/forge/sessions/sess-1/voice/client-secrets"
        assert channel._minter._client == broker._get_session_http_client
