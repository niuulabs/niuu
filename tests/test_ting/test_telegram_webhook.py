"""Tests for Telegram webhook command interface."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.adapters.inbound.rest_telegram_webhook import (
    HELP_TEXT,
    ParsedCommand,
    TelegramReplyClient,
    _dispatch_command,
    _find_run_by_tracker_id,
    create_telegram_webhook_router,
    parse_command,
)
from ting.config import ReviewConfig, TelegramConfig
from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    DispatcherState,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.domain.services.run_review import RunReviewService
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.notification_subscriptions import NotificationSubscriptionRepository
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrPort, VolundrSession

# ---------------------------------------------------------------------------
# Mock implementations
# ---------------------------------------------------------------------------


class StubNotificationSubRepo(NotificationSubscriptionRepository):
    """In-memory notification subscription repo for tests."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        # chat_id -> owner_id
        self._mapping = mapping or {}

    async def find_owner_by_telegram_chat_id(self, chat_id: str) -> str | None:
        return self._mapping.get(chat_id)


class StubSagaRepo(SagaRepository):
    def __init__(self, sagas: list[Saga] | None = None) -> None:
        self._sagas = sagas or []

    def add_saga(self, *, slug: str = "alpha", owner_id: str = "user-1") -> Saga:
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime
        from uuid import uuid4 as _uuid4

        # Replace any existing saga with the same slug to keep test setup
        # explicit — the fixture seeds a default saga, but tests that call
        # add_saga(slug=...) want their own version of that saga.
        self._sagas = [s for s in self._sagas if s.slug != slug]
        saga = Saga(
            id=_uuid4(),
            tracker_id=f"proj-{slug}",
            tracker_type="mock",
            slug=slug,
            name=slug.title(),
            repos=["org/repo"],
            feature_branch=f"feat/{slug}",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=_datetime.now(_UTC),
            base_branch="dev",
            owner_id=owner_id,
        )
        self._sagas.append(saga)
        return saga

    async def save_saga(self, saga: Saga, *, conn=None) -> None:  # noqa: ANN001
        pass

    async def save_phase(self, phase: object, *, conn=None) -> None:  # noqa: ANN001
        pass

    async def save_run(self, run: object, *, conn=None) -> None:  # noqa: ANN001
        pass

    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        if owner_id is None:
            return self._sagas
        return [s for s in self._sagas if s.owner_id == owner_id]

    async def get_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> Saga | None:
        return next((s for s in self._sagas if s.id == saga_id), None)

    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        return next((s for s in self._sagas if s.slug == slug), None)

    async def count_by_status(self) -> dict[str, int]:
        from ting.domain.models import RunStatus

        return {s.value: 0 for s in RunStatus}

    async def delete_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> bool:
        return False

    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        pass


class StubTracker(TrackerPort):
    """In-memory TrackerPort implementation for tests."""

    def __init__(self) -> None:
        self.runs: dict[UUID, Run] = {}
        self.events: dict[UUID, list[ConfidenceEvent]] = {}
        self._tracker_id_map: dict[str, Run] = {}
        self._phase_for_run: dict[str, object] = {}
        self._all_merged: bool = False

    def add_run(self, run: Run) -> None:
        self.runs[run.id] = run
        self._tracker_id_map[run.tracker_id] = run

    # -- CRUD: create entities --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return saga.tracker_id

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return phase.tracker_id

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        self.add_run(run)
        return run.tracker_id

    # -- CRUD: update / close --

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        pass

    async def close_run(self, run_id: str) -> None:
        pass

    # -- Read: fetch domain entities by tracker ID --

    async def get_saga(self, saga_id: str) -> Saga:
        raise ValueError(f"Not found: {saga_id}")

    async def get_phase(self, tracker_id: str) -> Phase:
        raise ValueError(f"Not found: {tracker_id}")

    async def get_run(self, tracker_id: str) -> Run:
        run = self._tracker_id_map.get(tracker_id)
        if run is None:
            raise ValueError(f"Run not found: {tracker_id}")
        return run

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return []

    # -- Browsing --

    async def list_projects(self) -> list[TrackerProject]:
        return []

    async def get_project(self, project_id: str) -> TrackerProject:
        raise ValueError(f"Not found: {project_id}")

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return []

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        return []

    # -- Run progress --

    async def update_run_progress(
        self,
        tracker_id: str,
        *,
        status: RunStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
    ) -> Run:
        run = self._tracker_id_map.get(tracker_id)
        if run is None:
            raise ValueError(f"Run not found: {tracker_id}")

        existing_events = self.events.get(run.id, [])
        new_confidence = existing_events[-1].score_after if existing_events else run.confidence

        updated = Run(
            id=run.id,
            phase_id=run.phase_id,
            tracker_id=run.tracker_id,
            name=run.name,
            description=run.description,
            acceptance_criteria=run.acceptance_criteria,
            declared_files=run.declared_files,
            estimate_hours=run.estimate_hours,
            status=status if status is not None else run.status,
            confidence=confidence if confidence is not None else new_confidence,
            session_id=session_id if session_id is not None else run.session_id,
            branch=run.branch,
            chronicle_summary=run.chronicle_summary,
            pr_url=pr_url if pr_url is not None else run.pr_url,
            pr_id=pr_id if pr_id is not None else run.pr_id,
            retry_count=retry_count if retry_count is not None else run.retry_count,
            created_at=run.created_at,
            updated_at=datetime.now(UTC),
        )
        self.runs[run.id] = updated
        self._tracker_id_map[tracker_id] = updated
        return updated

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return list(self.runs.values())

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return None

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return [r for r in self.runs.values() if r.status == status]

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    # -- Confidence events --

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        run = self._tracker_id_map.get(tracker_id)
        if run is not None:
            self.events.setdefault(run.id, []).append(event)

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        run = self._tracker_id_map.get(tracker_id)
        if run is None:
            return []
        return self.events.get(run.id, [])

    # -- Phase gate management --

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return self._all_merged

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    # -- Cross-entity navigation --

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return None

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return self._phase_for_run.get(tracker_id)  # type: ignore[return-value]

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return None

    # -- Session messages --

    async def save_session_message(self, message: SessionMessage) -> None:
        pass

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        return []


class StubTrackerFactory:
    def __init__(self, tracker: StubTracker) -> None:
        self._tracker = tracker

    async def for_owner(self, owner_id: str) -> list[StubTracker]:
        return [self._tracker]


class StubDispatcherRepo(DispatcherRepository):
    def __init__(self, running: bool = True) -> None:
        self._state = DispatcherState(
            id=uuid4(),
            owner_id="user-1",
            running=running,
            threshold=0.7,
            max_concurrent_runs=3,
            auto_continue=False,
            updated_at=datetime.now(UTC),
        )

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        return self._state

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        running = fields.get("running", self._state.running)
        self._state = DispatcherState(
            id=self._state.id,
            owner_id=owner_id,
            running=running,
            threshold=self._state.threshold,
            max_concurrent_runs=self._state.max_concurrent_runs,
            auto_continue=self._state.auto_continue,
            updated_at=datetime.now(UTC),
        )
        return self._state

    async def list_active_owner_ids(self) -> list[str]:
        return [self._state.owner_id] if self._state.running else []


class StubVolundr(VolundrPort):
    def __init__(self, sessions: list[VolundrSession] | None = None) -> None:
        self._sessions = sessions or []
        self.sent_messages: list[tuple[str, str]] = []

    async def spawn_session(self, request, *, auth_token=None):
        raise NotImplementedError

    async def get_session(self, session_id, *, auth_token=None):
        return next((s for s in self._sessions if s.id == session_id), None)

    async def list_sessions(self, *, auth_token=None):
        return self._sessions

    async def get_pr_status(self, session_id):
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id):
        return ""

    async def send_message(self, session_id, message, *, auth_token=None):
        self.sent_messages.append((session_id, message))

    async def stop_session(self, session_id, *, auth_token=None):
        pass

    async def list_integration_ids(self, *, auth_token=None) -> list[str]:
        return []

    async def list_repos(self, *, auth_token=None):
        return []

    async def get_conversation(self, session_id: str) -> dict:
        stub = '{"confidence": 0.9, "approved": true, "issues": []}'
        return {"turns": [{"role": "assistant", "content": stub}]}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return '{"confidence": 0.9, "approved": true, "summary": "stub", "issues": []}'

    async def subscribe_activity(self):
        return
        yield  # type: ignore[misc]  # pragma: no cover


class StubReplyClient(TelegramReplyClient):
    """Captures replies instead of making HTTP calls."""

    def __init__(self) -> None:
        super().__init__(bot_token="", timeout=5.0)
        self.replies: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.replies.append((chat_id, text))

    async def close(self) -> None:
        pass


class StubDispatchService:
    """Minimal stub for /dispatch and /list tests.

    Stores a fake ready queue and returns scripted DispatchResults — does not
    actually call Volundr/tracker. Tests inspect ``dispatched`` to verify
    the right items were forwarded.
    """

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.dispatched: list[tuple[list[Any], dict[str, Any]]] = []
        self.next_results: list[Any] = []

    async def find_ready_issues(
        self,
        owner_id: str,
        *,
        auth_token: str | None = None,
        saga_tracker_id: str | None = None,
    ) -> list[Any]:
        del owner_id, auth_token
        if saga_tracker_id is None:
            return list(self.queue)
        return [q for q in self.queue if q.saga_id == saga_tracker_id]

    async def dispatch_issues(
        self,
        owner_id: str,
        items: list[Any],
        **kwargs: Any,
    ) -> list[Any]:
        del owner_id
        self.dispatched.append((list(items), dict(kwargs)))
        return list(self.next_results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OWNER_ID = "user-1"
CHAT_ID = "123456"
BOT_TOKEN = "test-bot-token"
WEBHOOK_SECRET = "test-webhook-secret"


def _make_run(
    run_id: UUID | None = None,
    tracker_id: str = "NIU-221",
    status: RunStatus = RunStatus.REVIEW,
) -> Run:
    now = datetime.now(UTC)
    return Run(
        id=run_id or uuid4(),
        phase_id=uuid4(),
        tracker_id=tracker_id,
        name="Test run",
        description="A test run",
        acceptance_criteria=["it works"],
        declared_files=["src/main.py"],
        estimate_hours=2.0,
        status=status,
        confidence=0.55,
        session_id="session-1",
        branch="run/test",
        chronicle_summary="summary",
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=now,
        updated_at=now,
    )


def _make_saga(owner_id: str = OWNER_ID) -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="proj-1",
        tracker_type="mock",
        slug="alpha",
        name="Alpha",
        repos=["org/repo"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=datetime.now(UTC),
        base_branch="dev",
        owner_id=owner_id,
    )


def _make_update(text: str, chat_id: str = CHAT_ID) -> dict:
    """Build a minimal Telegram Update payload."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": int(chat_id), "first_name": "Test"},
            "chat": {"id": int(chat_id), "type": "private"},
            "text": text,
        },
    }


@pytest.fixture
def sub_repo() -> StubNotificationSubRepo:
    return StubNotificationSubRepo({CHAT_ID: OWNER_ID})


@pytest.fixture
def tracker() -> StubTracker:
    return StubTracker()


@pytest.fixture
def saga_repo() -> StubSagaRepo:
    return StubSagaRepo([_make_saga()])


@pytest.fixture
def dispatcher_repo() -> StubDispatcherRepo:
    return StubDispatcherRepo()


@pytest.fixture
def volundr() -> StubVolundr:
    return StubVolundr(
        [
            VolundrSession(
                id="sess-1",
                name="Alpha run 1",
                status="running",
                tracker_issue_id="NIU-221",
            ),
        ]
    )


@pytest.fixture
def reply_client() -> StubReplyClient:
    return StubReplyClient()


@pytest.fixture
def dispatch_service() -> StubDispatchService:
    return StubDispatchService()


@pytest.fixture
def client(
    sub_repo: StubNotificationSubRepo,
    tracker: StubTracker,
    saga_repo: StubSagaRepo,
    dispatcher_repo: StubDispatcherRepo,
    volundr: StubVolundr,
    reply_client: StubReplyClient,
    dispatch_service: StubDispatchService,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_telegram_webhook_router())

    app.state.notification_sub_repo = sub_repo
    app.state.tracker_factory = StubTrackerFactory(tracker)
    app.state.saga_repo = saga_repo
    app.state.dispatcher_repo = dispatcher_repo
    app.state.volundr = volundr
    app.state.telegram_reply_client = reply_client
    app.state.dispatch_service = dispatch_service
    app.state.settings = SimpleNamespace(
        telegram=TelegramConfig(
            bot_token=BOT_TOKEN,
            webhook_secret=WEBHOOK_SECRET,
        ),
        review=ReviewConfig(),
    )

    return TestClient(app)


def _post_webhook(client: TestClient, text: str, **kwargs) -> Any:
    """Helper to post a webhook update with the correct secret header."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}
    return client.post(
        "/api/v1/ting/telegram/webhook",
        json=_make_update(text, **kwargs),
        headers=headers,
    )


# ---------------------------------------------------------------------------
# parse_command tests
# ---------------------------------------------------------------------------


class TestParseCommand:
    def test_basic_command(self):
        cmd = parse_command("/status")
        assert cmd is not None
        assert cmd.name == "status"
        assert cmd.args == []

    def test_command_with_args(self):
        cmd = parse_command("/approve NIU-221")
        assert cmd is not None
        assert cmd.name == "approve"
        assert cmd.args == ["NIU-221"]

    def test_command_with_multiple_args(self):
        cmd = parse_command("/reject NIU-221 scope drift detected")
        assert cmd is not None
        assert cmd.name == "reject"
        assert cmd.args == ["NIU-221", "scope", "drift", "detected"]
        assert cmd.raw_text == "NIU-221 scope drift detected"

    def test_command_with_bot_mention(self):
        cmd = parse_command("/status@TingBot")
        assert cmd is not None
        assert cmd.name == "status"

    def test_non_command_returns_none(self):
        assert parse_command("hello world") is None

    def test_empty_string_returns_none(self):
        assert parse_command("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_command("   ") is None

    def test_command_case_insensitive(self):
        cmd = parse_command("/STATUS")
        assert cmd is not None
        assert cmd.name == "status"

    def test_command_with_leading_whitespace(self):
        cmd = parse_command("  /help")
        assert cmd is not None
        assert cmd.name == "help"


# ---------------------------------------------------------------------------
# Webhook secret validation
# ---------------------------------------------------------------------------


class TestWebhookSecretValidation:
    def test_missing_secret_header_returns_403(self, client: TestClient):
        """Request without X-Telegram-Bot-Api-Secret-Token is rejected."""
        resp = client.post(
            "/api/v1/ting/telegram/webhook",
            json=_make_update("/status"),
            # No secret header
        )
        assert resp.status_code == 403

    def test_wrong_secret_returns_403(self, client: TestClient):
        resp = client.post(
            "/api/v1/ting/telegram/webhook",
            json=_make_update("/status"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert resp.status_code == 403

    def test_correct_secret_passes(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/help")
        assert resp.status_code == 200
        assert len(reply_client.replies) == 1

    def test_empty_secret_config_allows_all(
        self,
        sub_repo: StubNotificationSubRepo,
        tracker: StubTracker,
        saga_repo: StubSagaRepo,
        dispatcher_repo: StubDispatcherRepo,
        volundr: StubVolundr,
        reply_client: StubReplyClient,
        dispatch_service: StubDispatchService,
    ):
        """When webhook_secret is empty in config, no validation occurs."""
        app = FastAPI()
        app.include_router(create_telegram_webhook_router())

        app.state.notification_sub_repo = sub_repo
        app.state.tracker_factory = StubTrackerFactory(tracker)
        app.state.saga_repo = saga_repo
        app.state.dispatcher_repo = dispatcher_repo
        app.state.volundr = volundr
        app.state.telegram_reply_client = reply_client
        app.state.dispatch_service = dispatch_service
        app.state.settings = SimpleNamespace(
            telegram=TelegramConfig(bot_token=BOT_TOKEN, webhook_secret=""),
            review=ReviewConfig(),
        )

        no_secret_client = TestClient(app)
        resp = no_secret_client.post(
            "/api/v1/ting/telegram/webhook",
            json=_make_update("/help"),
            # No secret header
        )
        assert resp.status_code == 200
        assert len(reply_client.replies) == 1


# ---------------------------------------------------------------------------
# Webhook endpoint tests
# ---------------------------------------------------------------------------


class TestWebhookEndpoint:
    def test_unauthenticated_chat(self, client: TestClient, reply_client: StubReplyClient):
        """Unlinked chat_id gets a 'not configured' message."""
        resp = client.post(
            "/api/v1/ting/telegram/webhook",
            json=_make_update("/status", chat_id="999999"),
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )
        assert resp.status_code == 200
        assert len(reply_client.replies) == 1
        assert "not linked" in reply_client.replies[0][1].lower()

    def test_no_message_in_update(self, client: TestClient, reply_client: StubReplyClient):
        """Updates without a message field are ignored."""
        resp = client.post(
            "/api/v1/ting/telegram/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )
        assert resp.status_code == 200
        assert len(reply_client.replies) == 0

    def test_empty_text_ignored(self, client: TestClient, reply_client: StubReplyClient):
        update = _make_update("")
        resp = client.post(
            "/api/v1/ting/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )
        assert resp.status_code == 200
        assert len(reply_client.replies) == 0

    def test_non_command_text_ignored(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "hello there")
        assert resp.status_code == 200
        assert len(reply_client.replies) == 0

    def test_unknown_command_returns_help(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/foobar")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "Unknown command" in reply
        assert "/foobar" in reply

    def test_help_command(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/help")
        assert resp.status_code == 200
        assert reply_client.replies[0][1] == HELP_TEXT

    def test_start_command_returns_help(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/start")
        assert resp.status_code == 200
        assert reply_client.replies[0][1] == HELP_TEXT


# ---------------------------------------------------------------------------
# /status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_shows_overview(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/status")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "*Dispatcher:* _running_" in reply
        assert "*Active sagas:* 1" in reply
        assert "Alpha" in reply
        assert "*Running sessions:* 1" in reply


# ---------------------------------------------------------------------------
# /approve command — now verifies confidence events are recorded
# ---------------------------------------------------------------------------


class TestApproveCommand:
    def test_approve_success_with_confidence_event(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run()
        tracker.add_run(run)

        resp = _post_webhook(client, "/approve NIU-221")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "approved" in reply.lower()
        assert "MERGED" in reply
        assert tracker.runs[run.id].status == RunStatus.MERGED

        # Verify confidence event was recorded
        events = tracker.events[run.id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.HUMAN_APPROVED
        assert events[0].delta == ReviewConfig().confidence_delta_approved

    def test_approve_no_args(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/approve")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]

    def test_approve_not_found(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/approve MISSING-1")
        assert resp.status_code == 200
        assert "not found" in reply_client.replies[0][1].lower()

    def test_approve_wrong_state(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run(status=RunStatus.PENDING)
        tracker.add_run(run)

        resp = _post_webhook(client, "/approve NIU-221")
        assert resp.status_code == 200
        assert "PENDING" in reply_client.replies[0][1]

    def test_approve_by_uuid(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run()
        tracker.add_run(run)

        resp = _post_webhook(client, f"/approve {run.id}")
        assert resp.status_code == 200
        assert "approved" in reply_client.replies[0][1].lower()


# ---------------------------------------------------------------------------
# /reject command — now verifies confidence events are recorded
# ---------------------------------------------------------------------------


class TestRejectCommand:
    def test_reject_success_with_confidence_event(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run()
        tracker.add_run(run)

        resp = _post_webhook(client, "/reject NIU-221 scope drift")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "rejected" in reply.lower()
        assert "FAILED" in reply
        assert "scope drift" in reply
        assert tracker.runs[run.id].status == RunStatus.FAILED

        # Verify confidence event was recorded
        events = tracker.events[run.id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.HUMAN_REJECT
        assert events[0].delta == ReviewConfig().confidence_delta_rejected

    def test_reject_no_reason(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run()
        tracker.add_run(run)

        resp = _post_webhook(client, "/reject NIU-221")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "rejected" in reply.lower()
        assert "reason" not in reply.lower()

    def test_reject_no_args(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/reject")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]

    def test_reject_wrong_state(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run(status=RunStatus.MERGED)
        tracker.add_run(run)

        resp = _post_webhook(client, "/reject NIU-221")
        assert resp.status_code == 200
        assert "MERGED" in reply_client.replies[0][1]


# ---------------------------------------------------------------------------
# /retry command — now verifies confidence events and retry_count
# ---------------------------------------------------------------------------


class TestRetryCommand:
    def test_retry_from_review_with_confidence_event(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run(status=RunStatus.REVIEW)
        tracker.add_run(run)

        resp = _post_webhook(client, "/retry NIU-221")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "retry" in reply.lower()
        assert "PENDING" in reply
        assert tracker.runs[run.id].status == RunStatus.PENDING
        assert tracker.runs[run.id].retry_count == 1

        # Verify confidence event was recorded
        events = tracker.events[run.id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.RETRY
        assert events[0].delta == ReviewConfig().confidence_delta_retry

    def test_retry_from_failed(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run(status=RunStatus.FAILED)
        tracker.add_run(run)

        resp = _post_webhook(client, "/retry NIU-221")
        assert resp.status_code == 200
        assert "QUEUED" in reply_client.replies[0][1]

    def test_retry_wrong_state(
        self,
        client: TestClient,
        tracker: StubTracker,
        reply_client: StubReplyClient,
    ):
        run = _make_run(status=RunStatus.RUNNING)
        tracker.add_run(run)

        resp = _post_webhook(client, "/retry NIU-221")
        assert resp.status_code == 200
        assert "RUNNING" in reply_client.replies[0][1]

    def test_retry_no_args(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/retry")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]


# ---------------------------------------------------------------------------
# /pause and /resume commands
# ---------------------------------------------------------------------------


class TestPauseResumeCommands:
    def test_pause_running_dispatcher(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/pause")
        assert resp.status_code == 200
        assert "paused" in reply_client.replies[0][1].lower()

    def test_pause_already_paused(
        self,
        client: TestClient,
        dispatcher_repo: StubDispatcherRepo,
        reply_client: StubReplyClient,
    ):
        dispatcher_repo._state = DispatcherState(
            id=uuid4(),
            owner_id=OWNER_ID,
            running=False,
            threshold=0.7,
            max_concurrent_runs=3,
            auto_continue=False,
            updated_at=datetime.now(UTC),
        )
        resp = _post_webhook(client, "/pause")
        assert resp.status_code == 200
        assert "already _paused_" in reply_client.replies[0][1].lower()

    def test_resume_paused_dispatcher(
        self,
        client: TestClient,
        dispatcher_repo: StubDispatcherRepo,
        reply_client: StubReplyClient,
    ):
        dispatcher_repo._state = DispatcherState(
            id=uuid4(),
            owner_id=OWNER_ID,
            running=False,
            threshold=0.7,
            max_concurrent_runs=3,
            auto_continue=False,
            updated_at=datetime.now(UTC),
        )
        resp = _post_webhook(client, "/resume")
        assert resp.status_code == 200
        assert "resumed" in reply_client.replies[0][1].lower()

    def test_resume_already_running(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/resume")
        assert resp.status_code == 200
        assert "already _running_" in reply_client.replies[0][1].lower()


# ---------------------------------------------------------------------------
# /dispatch command
# ---------------------------------------------------------------------------


def _make_queue_item(
    *,
    identifier: str = "NIU-221",
    saga_id: str = "saga-1",
    saga_slug: str = "alpha",
    repos: list[str] | None = None,
    title: str = "Test issue",
    phase_name: str = "Phase 1",
    url: str = "",
) -> Any:
    from ting.domain.services.dispatch_service import QueueItem

    return QueueItem(
        saga_id=saga_id,
        saga_name="Alpha",
        saga_slug=saga_slug,
        repos=repos or ["org/repo"],
        feature_branch="feat/alpha",
        phase_name=phase_name,
        issue_id=f"issue-{identifier}",
        identifier=identifier,
        title=title,
        description="",
        status="Todo",
        priority=1,
        priority_label="Urgent",
        estimate=2,
        url=url,
    )


def _make_dispatch_result(
    *,
    issue_id: str,
    session_id: str = "sess-abc",
    status: str = "spawned",
) -> Any:
    from ting.domain.services.dispatch_service import DispatchResult

    return DispatchResult(
        issue_id=issue_id,
        session_id=session_id,
        session_name="alpha-niu-221",
        status=status,
    )


class TestDispatchCommand:
    def test_dispatch_spawns_session(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        item = _make_queue_item(identifier="NIU-221", saga_id=str(saga.id))
        dispatch_service.queue = [item]
        dispatch_service.next_results = [_make_dispatch_result(issue_id=item.issue_id)]

        resp = _post_webhook(client, "/dispatch NIU-221")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "Dispatched" in reply
        assert "NIU-221" in reply
        assert "sess-abc" in reply
        assert dispatch_service.dispatched, "dispatch_issues should have been called"
        called_items, _ = dispatch_service.dispatched[0]
        assert called_items[0].saga_id == str(saga.id)

    def test_dispatch_case_insensitive_identifier(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        item = _make_queue_item(identifier="NIU-777", saga_id=str(saga.id))
        dispatch_service.queue = [item]
        dispatch_service.next_results = [_make_dispatch_result(issue_id=item.issue_id)]

        resp = _post_webhook(client, "/dispatch niu-777")
        assert resp.status_code == 200
        assert dispatch_service.dispatched, "lowercase identifier should still match"

    def test_dispatch_with_model_and_runtime(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        item = _make_queue_item(identifier="NIU-221", saga_id=str(saga.id))
        dispatch_service.queue = [item]
        dispatch_service.next_results = [_make_dispatch_result(issue_id=item.issue_id)]

        resp = _post_webhook(client, "/dispatch NIU-221 gpt-5.5 skuldCodex")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "model=" in reply and "gpt-5.5" in reply
        assert "runtime=" in reply and "skuldCodex" in reply

        called_items, called_kwargs = dispatch_service.dispatched[0]
        assert called_items[0].session_definition == "skuldCodex"
        assert called_kwargs.get("model") == "gpt-5.5"
        assert called_kwargs.get("session_definition") == "skuldCodex"

    def test_dispatch_not_ready(
        self,
        client: TestClient,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        dispatch_service.queue = []  # nothing ready

        resp = _post_webhook(client, "/dispatch NIU-221")
        assert resp.status_code == 200
        assert "not ready" in reply_client.replies[0][1].lower()
        assert not dispatch_service.dispatched

    def test_dispatch_failed_result(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        item = _make_queue_item(identifier="NIU-221", saga_id=str(saga.id))
        dispatch_service.queue = [item]
        dispatch_service.next_results = [
            _make_dispatch_result(issue_id=item.issue_id, status="failed")
        ]

        resp = _post_webhook(client, "/dispatch NIU-221")
        assert resp.status_code == 200
        assert "failed" in reply_client.replies[0][1].lower()

    def test_dispatch_no_args(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/dispatch")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]

    def test_dispatch_reply_uses_clickable_link(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        item = _make_queue_item(
            identifier="NIU-221",
            saga_id=str(saga.id),
            url="https://linear.app/team/issue/NIU-221",
        )
        dispatch_service.queue = [item]
        dispatch_service.next_results = [_make_dispatch_result(issue_id=item.issue_id)]

        resp = _post_webhook(client, "/dispatch NIU-221")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "[NIU-221](https://linear.app/team/issue/NIU-221)" in reply


# ---------------------------------------------------------------------------
# /list command
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_empty(
        self,
        client: TestClient,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        dispatch_service.queue = []
        resp = _post_webhook(client, "/list")
        assert resp.status_code == 200
        assert "No ready runs" in reply_client.replies[0][1]

    def test_list_default_limit(
        self,
        client: TestClient,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        dispatch_service.queue = [
            _make_queue_item(identifier=f"NIU-{i}", title=f"Issue {i}") for i in range(15)
        ]
        resp = _post_webhook(client, "/list")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        # Default limit is 10
        assert "showing 10 of 15" in reply
        assert "NIU-0" in reply
        assert "NIU-9" in reply
        assert "NIU-10" not in reply

    def test_list_with_limit(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga = saga_repo.add_saga(slug="alpha")
        dispatch_service.queue = [
            _make_queue_item(identifier=f"NIU-{i}", saga_id=saga.tracker_id) for i in range(15)
        ]
        resp = _post_webhook(client, "/list alpha 3")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "showing 3 of 15" in reply

    def test_list_scoped_to_saga(
        self,
        client: TestClient,
        saga_repo: StubSagaRepo,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        saga_alpha = saga_repo.add_saga(slug="alpha")
        saga_beta = saga_repo.add_saga(slug="beta")
        dispatch_service.queue = [
            _make_queue_item(identifier="NIU-1", saga_id=saga_alpha.tracker_id, saga_slug="alpha"),
            _make_queue_item(identifier="NIU-2", saga_id=saga_beta.tracker_id, saga_slug="beta"),
        ]
        resp = _post_webhook(client, "/list alpha")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "NIU-1" in reply
        assert "NIU-2" not in reply

    def test_list_unknown_saga(
        self,
        client: TestClient,
        reply_client: StubReplyClient,
    ):
        resp = _post_webhook(client, "/list does-not-exist")
        assert resp.status_code == 200
        assert "Saga not found" in reply_client.replies[0][1]

    def test_list_invalid_limit(
        self,
        client: TestClient,
        reply_client: StubReplyClient,
    ):
        resp = _post_webhook(client, "/list alpha not-a-number")
        assert resp.status_code == 200
        assert "Invalid limit" in reply_client.replies[0][1]

    def test_list_renders_url_as_clickable_link(
        self,
        client: TestClient,
        dispatch_service: StubDispatchService,
        reply_client: StubReplyClient,
    ):
        dispatch_service.queue = [
            _make_queue_item(
                identifier="NIU-100",
                title="Fix auth",
                url="https://linear.app/team/issue/NIU-100",
            ),
            _make_queue_item(
                identifier="NIU-101",
                title="No url",
                url="",
            ),
        ]
        resp = _post_webhook(client, "/list")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        # Markdown link for the one with a URL
        assert "[NIU-100](https://linear.app/team/issue/NIU-100)" in reply
        # Backticks fallback for the one without
        assert "`NIU-101`" in reply


# ---------------------------------------------------------------------------
# /sessions command
# ---------------------------------------------------------------------------


class TestSessionsCommand:
    def test_sessions_lists_running(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/sessions")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "sess-1" in reply
        assert "Alpha run 1" in reply

    def test_sessions_empty(
        self,
        client: TestClient,
        volundr: StubVolundr,
        reply_client: StubReplyClient,
    ):
        volundr._sessions = []
        resp = _post_webhook(client, "/sessions")
        assert resp.status_code == 200
        assert "No running sessions" in reply_client.replies[0][1]


# ---------------------------------------------------------------------------
# /say command
# ---------------------------------------------------------------------------


class TestSayCommand:
    def test_say_sends_message(
        self,
        client: TestClient,
        volundr: StubVolundr,
        reply_client: StubReplyClient,
    ):
        resp = _post_webhook(client, "/say sess-1 fix the failing test")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "Message sent" in reply
        assert volundr.sent_messages == [("sess-1", "fix the failing test")]

    def test_say_session_not_found(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/say nonexistent hello")
        assert resp.status_code == 200
        assert "not found" in reply_client.replies[0][1].lower()

    def test_say_no_args(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/say")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]

    def test_say_missing_message(self, client: TestClient, reply_client: StubReplyClient):
        resp = _post_webhook(client, "/say sess-1")
        assert resp.status_code == 200
        assert "Usage" in reply_client.replies[0][1]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_command_handler_exception(self, client: TestClient, reply_client: StubReplyClient):
        """If a command handler throws, we reply with an error message."""
        # Break the saga_repo to force an exception in /status
        client.app.state.saga_repo = None

        resp = _post_webhook(client, "/status")
        assert resp.status_code == 200
        reply = reply_client.replies[0][1]
        assert "Error" in reply


# ---------------------------------------------------------------------------
# TelegramReplyClient unit tests
# ---------------------------------------------------------------------------


class TestTelegramReplyClient:
    @pytest.mark.asyncio
    async def test_no_token_logs_warning(self):
        """When bot_token is empty, no HTTP call is made."""
        rc = TelegramReplyClient(bot_token="", timeout=5.0)
        await rc.send("12345", "hello")
        await rc.close()

    @pytest.mark.asyncio
    async def test_http_error_suppressed(self):
        """Network errors during reply do not propagate."""
        rc = TelegramReplyClient(bot_token="token", timeout=5.0)
        # Patch the internal client to fail
        rc._client = AsyncMock()
        rc._client.post.side_effect = httpx.ConnectError("fail")
        # Should not raise
        await rc.send("12345", "hello")

    @pytest.mark.asyncio
    async def test_close_closes_client(self):
        rc = TelegramReplyClient(bot_token="token", timeout=5.0)
        await rc.close()


# ---------------------------------------------------------------------------
# _find_run_by_tracker_id unit tests
# ---------------------------------------------------------------------------


class TestFindRunByTrackerId:
    @pytest.mark.asyncio
    async def test_uuid_lookup(self):
        stub = StubTracker()
        run = _make_run()
        stub.add_run(run)

        found = await _find_run_by_tracker_id(stub, str(run.id), OWNER_ID)
        assert found is not None
        assert found.id == run.id

    @pytest.mark.asyncio
    async def test_tracker_id_lookup(self):
        stub = StubTracker()
        run = _make_run(tracker_id="NIU-300")
        stub.add_run(run)

        found = await _find_run_by_tracker_id(stub, "NIU-300", OWNER_ID)
        assert found is not None
        assert found.tracker_id == "NIU-300"

    @pytest.mark.asyncio
    async def test_not_found(self):
        stub = StubTracker()
        found = await _find_run_by_tracker_id(stub, "MISSING-1", OWNER_ID)
        assert found is None


# ---------------------------------------------------------------------------
# _dispatch_command unit tests
# ---------------------------------------------------------------------------


class TestDispatchCommandFunction:
    @pytest.mark.asyncio
    async def test_unknown_command(self):
        stub_tracker = StubTracker()
        result = await _dispatch_command(
            OWNER_ID,
            ParsedCommand(name="xyz", args=[], raw_text=""),
            tracker=stub_tracker,
            saga_repo=StubSagaRepo(),
            volundr=StubVolundr(),
            dispatcher_repo=StubDispatcherRepo(),
            dispatch_service=StubDispatchService(),
            review_service=RunReviewService(stub_tracker, OWNER_ID, ReviewConfig()),
        )
        assert "Unknown command" in result
        assert "/xyz" in result
