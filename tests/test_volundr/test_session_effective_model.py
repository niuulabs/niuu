"""A session's reported model follows the model the runtime last put in effect (B1d)."""

import asyncio
from datetime import UTC, datetime, timedelta

from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.test_domain.test_session_usage import Projection, claude
from volundr.adapters.inbound.rest import SessionResponse, SessionStatusResponse
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import EventType, Session, SessionStatus
from volundr.domain.services.session import SessionService
from volundr.domain.session_usage import observation

AT = datetime(2026, 10, 26, tzinfo=UTC)


def _response(identity: str, model: str) -> dict:
    payload = claude(identity)
    payload["message"]["model"] = model
    return payload


def _runtime_options(model: str, *, applied: bool = True, active_turn=None) -> dict:
    return {
        "type": "runtime_options",
        "request_id": "picker",
        "applied": applied,
        "source": "catalog",
        "applies_to": "next_turn",
        "models": [{"model": model}],
        "current": {"model": model, "effort": "high", "service_tier": None},
        "active_turn": active_turn,
    }


def test_a_response_on_a_new_model_changes_the_projected_model():
    projection = Projection()
    projection.feed(_response("one", "claude-fable-5-1"))
    assert projection.state["model"] == "claude-fable-5-1"
    # The user typed /model in the terminal: the next response names the new model.
    projection.feed(_response("two", "claude-opus-5-5"))
    assert projection.state["model"] == "claude-opus-5-5"


def test_synthetic_rows_never_name_the_model():
    projection = Projection()
    projection.feed(_response("one", "claude-opus-5-5"))
    projection.feed(_response("two", "<synthetic>"))
    assert projection.state["model"] == "claude-opus-5-5"


def test_an_acknowledged_change_between_turns_is_a_model_observation():
    event = observation(_runtime_options("claude-opus-5-5"), seq=7, at=AT)
    assert event["kind"] == "model"
    assert event["model"] == "claude-opus-5-5"
    projection = Projection()
    projection.feed(_response("one", "claude-fable-5-1"))
    projection.feed(_runtime_options("claude-opus-5-5"))
    assert projection.state["model"] == "claude-opus-5-5"


def test_reads_and_changes_during_a_turn_are_not_observations():
    assert observation(_runtime_options("claude-opus-5-5", applied=False), seq=1, at=AT) is None
    busy = _runtime_options("claude-opus-5-5", active_turn={"id": "t1", "model": "x"})
    assert observation(busy, seq=1, at=AT) is None
    unnamed = _runtime_options("claude-opus-5-5")
    unnamed["current"] = {"model": ""}
    assert observation(unnamed, seq=1, at=AT) is None


def test_a_replayed_older_response_cannot_revert_an_acknowledged_change():
    projection = Projection()
    old = _response("one", "claude-fable-5-1")
    projection.feed(old, at=AT, seq=1)
    projection.feed(_runtime_options("claude-opus-5-5"), at=AT + timedelta(seconds=5), seq=2)
    projection.feed(old, at=AT, seq=3)
    assert projection.state["model"] == "claude-opus-5-5"


def test_an_unchanged_model_leaves_the_projection_untouched():
    projection = Projection()
    projection.feed(_response("one", "claude-opus-5-5"))
    before = projection.state
    projection.feed(_runtime_options("claude-opus-5-5"))
    assert projection.state == before


def test_every_reader_reports_the_model_in_effect_and_keeps_the_launch_record():
    session = Session(
        name="model",
        model="claude-fable-5-1",
        status=SessionStatus.RUNNING,
        session_usage={"model": "claude-opus-5-5"},
    )
    assert session.model == "claude-fable-5-1"  # launch config and restart read this
    assert session.effective_model == "claude-opus-5-5"
    assert SessionResponse.from_session(session).model == "claude-opus-5-5"
    assert SessionStatusResponse.from_session(session).model == "claude-opus-5-5"
    event = InMemoryEventBroadcaster().create_session_event(EventType.SESSION_UPDATED, session)
    assert event.data["model"] == "claude-opus-5-5"


def test_without_a_reported_model_the_launch_model_is_shown():
    session = Session(name="fresh", model="claude-fable-5-1", status=SessionStatus.RUNNING)
    assert session.effective_model == "claude-fable-5-1"
    assert SessionResponse.from_session(session).model == "claude-fable-5-1"


async def test_a_model_change_publishes_one_session_updated_carrying_it():
    repository = InMemorySessionRepository()
    broadcaster = InMemoryEventBroadcaster()
    service = SessionService(repository, MockPodManager(), broadcaster=broadcaster)
    session = await repository.create(
        Session(name="live", model="claude-fable-5-1", status=SessionStatus.RUNNING)
    )
    stream = broadcaster.subscribe()
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    try:
        # The usage projection is written by the event-log adapter, not update().
        repository._sessions[session.id] = session.model_copy(
            update={"session_usage": {"model": "claude-opus-5-5"}}
        )
        await service.notify_usage_changed(session.id)
        event = await asyncio.wait_for(waiting, timeout=2)
        assert event.type.value == "session_updated"
        assert event.data["model"] == "claude-opus-5-5"
        # One change, one event: nothing else is queued behind it.
        waiting = asyncio.create_task(anext(stream))
        done, _ = await asyncio.wait({waiting}, timeout=0.05)
        assert not done
    finally:
        if not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await stream.aclose()
