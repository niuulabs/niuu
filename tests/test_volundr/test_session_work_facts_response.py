"""F12d: Forge serves last_work_at, tool_open and open_controls on the session."""

from datetime import UTC, datetime
from uuid import uuid4

from volundr.adapters.inbound.rest import SessionResponse
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import EventType, GitSource, Session, SessionStatus


def _session(**kw) -> Session:
    return Session(
        id=uuid4(),
        name="g10",
        model="test",
        source=GitSource(repo="https://example.test/repo", branch="main"),
        status=SessionStatus.RUNNING,
        **kw,
    )


def test_response_serves_work_facts_and_open_controls():
    at = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
    controls = [{"request_id": "q-1", "kind": "question", "opened_at": "2026-10-21T10:31:00+00:00"}]
    session = _session(
        last_work_at=at, open_tool_count=2, activity_metadata={"open_controls": controls}
    )
    body = SessionResponse.from_session(session, people_count=1).model_dump()
    assert body["last_work_at"] == at.isoformat()
    assert body["tool_open"] is True
    assert body["open_controls"] == controls


def test_unknown_controls_stay_null_never_empty():
    body = SessionResponse.from_session(_session(), people_count=1).model_dump()
    assert body["last_work_at"] is None
    assert body["tool_open"] is False
    assert body["open_controls"] is None
    assert _session(activity_metadata={"open_controls": []}).open_controls == []
    junk = {"open_controls": [{"kind": "question"}, "x", {"request_id": "p", "kind": "permission"}]}
    assert _session(activity_metadata=junk).open_controls == [
        {"request_id": "p", "kind": "permission"}
    ]


def test_session_updated_event_carries_the_facts():
    at = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
    session = _session(last_work_at=at, open_tool_count=1, activity_metadata={"open_controls": []})
    event = InMemoryEventBroadcaster().create_session_event(EventType.SESSION_UPDATED, session)
    assert event.data["last_work_at"] == at.isoformat()
    assert event.data["tool_open"] is True
    assert event.data["open_controls"] == []


def test_open_controls_carry_the_clis_tool_use_id_when_known():
    """LEX-1979: the app ties a transcript question (toolu_…) to its card."""
    controls = [
        {
            "request_id": "tty-1",
            "kind": "question",
            "opened_at": "2026-11-08T02:21:56+00:00",
            "tool_use_id": "toolu_01FNSi1eEKu7LVLCvQ9qL51y",
        },
        {"request_id": "p-1", "kind": "permission", "opened_at": "2026-11-08T02:22:00+00:00"},
    ]
    body = SessionResponse.from_session(
        _session(activity_metadata={"open_controls": controls}), people_count=1
    ).model_dump()
    assert body["open_controls"] == controls
