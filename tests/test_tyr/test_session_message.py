"""Tests for session message sending — domain service and REST API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.api.raids import (
    create_raids_router,
    resolve_git,
    resolve_tracker,
    resolve_volundr,
)
from tyr.config import AuthConfig, ReviewConfig
from tyr.domain.exceptions import RaidNotFoundError
from tyr.domain.models import (
    ConfidenceEventType,
    RaidStatus,
    SessionMessage,
)
from tyr.domain.services.session_message import (
    NoActiveSessionError,
    RaidNotRunningError,
    SessionMessageService,
)

from .test_raids_api import (
    MockGit,
    MockVolundr,
    StatefulMockTracker,
    _make_phase,
    _make_raid,
    _make_saga,
)

REVIEW_CFG = ReviewConfig()


# ---------------------------------------------------------------------------
# Extend MockVolundr to track sent messages
# ---------------------------------------------------------------------------


class MessageTrackingVolundr(MockVolundr):
    """MockVolundr that tracks messages sent to sessions."""

    def __init__(self) -> None:
        super().__init__()
        self.sent_messages: list[tuple[str, str, str | None]] = []
        self.directed_messages: list[tuple[str, str, str, str | None]] = []
        self.fail_send_message = False

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        if self.fail_send_message:
            raise ConnectionError("Volundr unreachable")
        self.sent_messages.append((session_id, message, auth_token))

    async def send_directed_room_message(
        self,
        session_id: str,
        target_peer_id: str,
        message: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        if self.fail_send_message:
            raise ConnectionError("Volundr unreachable")
        self.directed_messages.append((session_id, target_peer_id, message, auth_token))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker() -> StatefulMockTracker:
    t = StatefulMockTracker()
    t.saga = _make_saga()
    t.phase = _make_phase()
    return t


@pytest.fixture
def volundr() -> MessageTrackingVolundr:
    return MessageTrackingVolundr()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def service(
    tracker: StatefulMockTracker,
    volundr: MessageTrackingVolundr,
    event_bus: InMemoryEventBus,
) -> SessionMessageService:
    return SessionMessageService(tracker, volundr, event_bus=event_bus)


@pytest.fixture
def client(
    tracker: StatefulMockTracker,
    volundr: MessageTrackingVolundr,
    event_bus: InMemoryEventBus,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_raids_router())
    app.dependency_overrides[resolve_tracker] = lambda: tracker
    app.dependency_overrides[resolve_volundr] = lambda: volundr
    app.dependency_overrides[resolve_git] = lambda: MockGit()

    app.state.settings = SimpleNamespace(
        review=REVIEW_CFG,
        auth=AuthConfig(allow_anonymous_dev=True),
    )
    app.state.event_bus = event_bus

    return TestClient(app)


# ---------------------------------------------------------------------------
# Domain service tests
# ---------------------------------------------------------------------------


class TestSessionMessageService:
    @pytest.mark.asyncio
    async def test_send_message_success(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        result = await service.send_message(raid.id, "Fix the failing test")

        assert result.raid_id == raid.id
        assert result.session_id == "session-1"
        assert result.message.content == "Fix the failing test"
        assert result.message.sender == "user"

        # Verify message was sent to Volundr
        assert len(volundr.sent_messages) == 1
        assert volundr.sent_messages[0] == ("session-1", "Fix the failing test", None)

        # Verify audit record persisted
        messages = tracker.messages.get(raid.id, [])
        assert len(messages) == 1
        assert messages[0].content == "Fix the failing test"

        # Verify confidence event recorded
        events = tracker.events.get(raid.tracker_id, [])
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.MESSAGE_SENT
        assert events[0].delta == 0.0
        assert events[0].score_after == raid.confidence

    @pytest.mark.asyncio
    async def test_send_message_with_auth_token(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        await service.send_message(raid.id, "hello", auth_token="pat-123")

        assert volundr.sent_messages[0][2] == "pat-123"

    @pytest.mark.asyncio
    async def test_send_message_custom_sender(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        result = await service.send_message(raid.id, "auto-retry", sender="tyr:watcher")

        assert result.message.sender == "tyr:watcher"

    @pytest.mark.asyncio
    async def test_send_message_in_review_state(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.REVIEW)
        tracker.raids[raid.id] = raid

        result = await service.send_message(raid.id, "feedback")

        assert result.message.content == "feedback"

    @pytest.mark.asyncio
    async def test_send_message_directs_reply_to_target_peer(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.ESCALATED)
        tracker.raids[raid.id] = raid

        result = await service.send_message(
            raid.id,
            "Please prioritize evidence from the vendor docs.",
            target_peer_id="flock-council-chair",
        )

        assert result.message.content == "Please prioritize evidence from the vendor docs."
        assert volundr.sent_messages == []
        assert volundr.directed_messages == [
            (
                "session-1",
                "flock-council-chair",
                "Please prioritize evidence from the vendor docs.",
                None,
            )
        ]

    @pytest.mark.asyncio
    async def test_send_message_raid_not_found(
        self,
        service: SessionMessageService,
    ):
        with pytest.raises(RaidNotFoundError):
            await service.send_message(uuid4(), "hello")

    @pytest.mark.asyncio
    async def test_send_message_raid_not_running(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.PENDING)
        tracker.raids[raid.id] = raid

        with pytest.raises(RaidNotRunningError) as exc_info:
            await service.send_message(raid.id, "hello")
        assert exc_info.value.status == "PENDING"

    @pytest.mark.asyncio
    async def test_send_message_no_active_session(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING, session_id=None)
        tracker.raids[raid.id] = raid

        with pytest.raises(NoActiveSessionError):
            await service.send_message(raid.id, "hello")

    @pytest.mark.asyncio
    async def test_send_message_merged_state_rejected(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.MERGED)
        tracker.raids[raid.id] = raid

        with pytest.raises(RaidNotRunningError):
            await service.send_message(raid.id, "hello")

    @pytest.mark.asyncio
    async def test_send_message_failed_state_rejected(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.FAILED)
        tracker.raids[raid.id] = raid

        with pytest.raises(RaidNotRunningError):
            await service.send_message(raid.id, "hello")

    @pytest.mark.asyncio
    async def test_send_message_emits_event(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
        event_bus: InMemoryEventBus,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        q = event_bus.subscribe()
        await service.send_message(raid.id, "CI failed on test_auth.py")

        event = q.get_nowait()
        assert event.event == "session.message_sent"
        assert event.data["raid_id"] == str(raid.id)
        assert event.data["session_id"] == "session-1"
        assert event.data["sender"] == "user"
        assert event.data["content_length"] == len("CI failed on test_auth.py")
        assert event.data["target_peer_id"] == ""

    @pytest.mark.asyncio
    async def test_send_message_no_event_bus(
        self,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        svc = SessionMessageService(tracker, volundr, event_bus=None)
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        result = await svc.send_message(raid.id, "hello")
        assert result.message.content == "hello"

    @pytest.mark.asyncio
    async def test_send_message_volundr_failure_propagates(
        self,
        service: SessionMessageService,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid
        volundr.fail_send_message = True

        with pytest.raises(ConnectionError):
            await service.send_message(raid.id, "hello")

        # No audit record should be persisted on failure
        messages = tracker.messages.get(raid.id, [])
        assert len(messages) == 0


# ---------------------------------------------------------------------------
# REST API tests — POST /raids/{id}/message
# ---------------------------------------------------------------------------


class TestSendMessageEndpoint:
    def test_send_message_success(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": "CI failed: see logs"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["raid_id"] == str(raid.id)
        assert data["session_id"] == "session-1"
        assert data["content"] == "CI failed: see logs"
        assert data["sender"] == "user"
        assert "message_id" in data
        assert "created_at" in data

        # Verify Volundr was called
        assert len(volundr.sent_messages) == 1

    def test_send_message_not_found(self, client: TestClient):
        resp = client.post(
            f"/api/v1/tyr/raids/{uuid4()}/message",
            json={"content": "hello"},
        )
        assert resp.status_code == 404

    def test_send_message_not_running(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.PENDING)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": "hello"},
        )
        assert resp.status_code == 409
        assert "PENDING" in resp.json()["detail"]

    def test_send_message_no_session(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING, session_id=None)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": "hello"},
        )
        assert resp.status_code == 409
        assert "no active session" in resp.json()["detail"]

    def test_send_message_empty_content(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": ""},
        )
        assert resp.status_code == 422

    def test_send_message_missing_content(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={},
        )
        assert resp.status_code == 422

    def test_send_message_review_state(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.REVIEW)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": "looks good but fix X"},
        )
        assert resp.status_code == 200

    def test_send_message_with_target_peer_id(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
        volundr: MessageTrackingVolundr,
    ):
        raid = _make_raid(status=RaidStatus.ESCALATED)
        tracker.raids[raid.id] = raid

        resp = client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={
                "content": "Use the permanent board as the final answer.",
                "target_peer_id": "flock-council-chair",
            },
        )

        assert resp.status_code == 200
        assert volundr.directed_messages == [
            (
                "session-1",
                "flock-council-chair",
                "Use the permanent board as the final answer.",
                None,
            )
        ]


# ---------------------------------------------------------------------------
# REST API tests — GET /raids/{id}/messages
# ---------------------------------------------------------------------------


class TestListMessagesEndpoint:
    def test_list_messages_empty(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        resp = client.get(f"/api/v1/tyr/raids/{raid.id}/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_messages_resolves_internal_raid_uuid_when_tracker_lookup_uses_external_id(
        self,
        volundr: MessageTrackingVolundr,
        event_bus: InMemoryEventBus,
    ):
        class ExternalOnlyTracker(StatefulMockTracker):
            async def get_raid(self, tracker_id: str):  # noqa: ANN001
                for raid in self.raids.values():
                    if raid.tracker_id == tracker_id:
                        return raid
                raise RaidNotFoundError(f"Raid not found: {tracker_id}")

        tracker = ExternalOnlyTracker()
        tracker.saga = _make_saga()
        tracker.phase = _make_phase()
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        app = FastAPI()
        app.include_router(create_raids_router())
        app.dependency_overrides[resolve_tracker] = lambda: tracker
        app.dependency_overrides[resolve_volundr] = lambda: volundr
        app.dependency_overrides[resolve_git] = lambda: MockGit()
        app.state.settings = SimpleNamespace(
            review=REVIEW_CFG,
            auth=AuthConfig(allow_anonymous_dev=True),
        )
        app.state.event_bus = event_bus

        client = TestClient(app)

        resp = client.get(f"/api/v1/tyr/raids/{raid.id}/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_messages_with_history(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        now = datetime.now(UTC)
        msg1 = SessionMessage(
            id=uuid4(),
            raid_id=raid.id,
            session_id="session-1",
            content="Fix test_auth.py",
            sender="user",
            created_at=now,
        )
        msg2 = SessionMessage(
            id=uuid4(),
            raid_id=raid.id,
            session_id="session-1",
            content="Also fix coverage",
            sender="tyr:watcher",
            created_at=now,
        )
        tracker.messages[raid.id] = [msg1, msg2]

        resp = client.get(f"/api/v1/tyr/raids/{raid.id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["content"] == "Fix test_auth.py"
        assert data[0]["sender"] == "user"
        assert data[1]["content"] == "Also fix coverage"
        assert data[1]["sender"] == "tyr:watcher"

    def test_list_messages_not_found(self, client: TestClient):
        resp = client.get(f"/api/v1/tyr/raids/{uuid4()}/messages")
        assert resp.status_code == 404

    def test_send_then_list(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker.raids[raid.id] = raid

        # Send a message
        client.post(
            f"/api/v1/tyr/raids/{raid.id}/message",
            json={"content": "hello from test"},
        )

        # List messages
        resp = client.get(f"/api/v1/tyr/raids/{raid.id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["content"] == "hello from test"

    def test_list_messages_parses_help_request_payload(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        raid = _make_raid(status=RaidStatus.ESCALATED)
        tracker.raids[raid.id] = raid

        payload = {
            "summary": "Need a product decision",
            "reason": "needs_feedback",
            "attempted": ["Compared A and B"],
            "recommendation": "Pick one direction",
            "context": {"slug": "research/council"},
            "target_peer_id": "flock-council-chair",
            "persona": "council-chair",
        }
        tracker.messages[raid.id] = [
            SessionMessage(
                id=uuid4(),
                raid_id=raid.id,
                session_id="session-1",
                content=json.dumps(payload),
                sender="help_needed",
                created_at=datetime.now(UTC),
            )
        ]

        resp = client.get(f"/api/v1/tyr/raids/{raid.id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["kind"] == "help_request"
        assert data[0]["help_request"]["summary"] == "Need a product decision"
        assert data[0]["help_request"]["target_peer_id"] == "flock-council-chair"
        assert data[0]["help_request"]["persona"] == "council-chair"


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestSessionMessageModel:
    def test_frozen_dataclass(self):
        msg = SessionMessage(
            id=uuid4(),
            raid_id=uuid4(),
            session_id="ses-1",
            content="hello",
            sender="user",
            created_at=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            msg.content = "changed"  # type: ignore[misc]

    def test_message_sent_event_type(self):
        assert ConfidenceEventType.MESSAGE_SENT == "message_sent"
        assert ConfidenceEventType.MESSAGE_SENT.value == "message_sent"
