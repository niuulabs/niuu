from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from sleipnir.domain.events import SleipnirEvent
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.adapters.ravn_help_needed_handler import RavnHelpNeededHandler
from ting.domain.models import RunStatus

from .test_runs_api import StatefulMockTracker, _make_run, _make_saga


class _DummySubscriber:
    async def subscribe(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("subscribe should not be used in this test")


class _Subscription:
    async def unsubscribe(self) -> None:
        return None


class _RecordingSubscriber:
    def __init__(self) -> None:
        self.event_types: list[str] | None = None

    async def subscribe(self, event_types, _callback):  # noqa: ANN001, ANN201
        self.event_types = list(event_types)
        return _Subscription()


class _TrackerFactory:
    def __init__(self, tracker: StatefulMockTracker) -> None:
        self._tracker = tracker

    async def for_owner(self, owner_id: str):  # noqa: ANN201
        assert owner_id == "dev-user"
        return [self._tracker]


def _event(*, session_id: str) -> SleipnirEvent:
    return SleipnirEvent(
        event_type="ravn.help.needed",
        source="ravn:flock-council-chair",
        payload={
            "session_id": session_id,
            "summary": "Need your call on the final recommendation.",
            "reason": "needs_feedback",
            "attempted": ["Compared the top two options", "Wrote a draft synthesis"],
            "recommendation": "Choose the rollout order.",
            "context": {"slug": "research/council-human-v1"},
            "persona": "council-chair",
        },
        summary="Need your call on the final recommendation.",
        urgency=0.9,
        domain="business",
        timestamp=datetime.now(UTC),
        correlation_id=session_id,
    )


@pytest.mark.asyncio
async def test_handler_persists_help_needed_and_emits_notification_event() -> None:
    tracker = StatefulMockTracker()
    run = _make_run(status=RunStatus.ESCALATED, session_id="session-council-1")
    tracker.runs[run.id] = run
    tracker.saga = _make_saga()
    tracker.get_run_by_session = AsyncMock(return_value=run)  # type: ignore[method-assign]
    event_bus = InMemoryEventBus()
    queue = event_bus.subscribe()
    handler = RavnHelpNeededHandler(
        subscriber=_DummySubscriber(),
        tracker_factory=_TrackerFactory(tracker),
        event_bus=event_bus,
        owner_id="dev-user",
    )

    await handler._process_event(_event(session_id="session-council-1"))  # noqa: SLF001

    messages = tracker.messages[run.id]
    assert len(messages) == 1
    assert messages[0].sender == "help_needed"
    assert '"target_peer_id": "flock-council-chair"' in messages[0].content

    emitted = queue.get_nowait()
    assert emitted.event == "run.feedback_requested"
    assert emitted.owner_id == "dev-user"
    assert emitted.data["run_id"] == str(run.id)
    assert emitted.data["summary"] == "Need your call on the final recommendation."
    assert emitted.data["ui_path"] == f"/ting/sagas/{tracker.saga.id}"


@pytest.mark.asyncio
async def test_handler_subscribes_to_canonical_and_translated_help_needed_events() -> None:
    subscriber = _RecordingSubscriber()
    tracker = StatefulMockTracker()
    event_bus = InMemoryEventBus()
    handler = RavnHelpNeededHandler(
        subscriber=subscriber,
        tracker_factory=_TrackerFactory(tracker),
        event_bus=event_bus,
        owner_id="dev-user",
    )

    await handler.start()
    await handler.stop()

    assert subscriber.event_types == ["help_needed", "ravn.help.needed"]


@pytest.mark.asyncio
async def test_handler_dedupes_duplicate_open_requests() -> None:
    tracker = StatefulMockTracker()
    run = _make_run(status=RunStatus.ESCALATED, session_id="session-council-2")
    tracker.runs[run.id] = run
    tracker.saga = _make_saga()
    tracker.get_run_by_session = AsyncMock(return_value=run)  # type: ignore[method-assign]
    event_bus = InMemoryEventBus()
    handler = RavnHelpNeededHandler(
        subscriber=_DummySubscriber(),
        tracker_factory=_TrackerFactory(tracker),
        event_bus=event_bus,
        owner_id="dev-user",
    )

    event = _event(session_id="session-council-2")
    await handler._process_event(event)  # noqa: SLF001
    await handler._process_event(event)  # noqa: SLF001

    assert len(tracker.messages[run.id]) == 1
