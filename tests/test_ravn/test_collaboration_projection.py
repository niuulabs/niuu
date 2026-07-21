"""Tests for Ravn-owned collaboration event projection."""

from datetime import UTC, datetime

from ravn.adapters.collaboration import project_ravn_event
from ravn.domain.events import RavnEvent, RavnEventType


def _event(event_type: RavnEventType, payload: dict) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="ravn-1",
        payload=payload,
        timestamp=datetime.now(UTC),
        urgency=0.8,
        correlation_id="corr-1",
        session_id="session-1",
        root_correlation_id="root-1",
        trace_context={"traceparent": "00-abc-def-01"},
    )


def test_help_projection_carries_exact_resume_context() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.HELP_NEEDED,
            {
                "reason": "missing_authority",
                "summary": "Approval required",
                "context": {"case_id": "case-1"},
            },
        ),
        persona="Resident",
    )[0]

    assert projected["kind"] == "notification"
    assert projected["notificationType"] == "help_needed"
    assert projected["replyContext"]["help_context"] == {"case_id": "case-1"}
    assert projected["replyContext"]["correlation_id"] == "corr-1"
    assert projected["replyContext"]["trace_context"] == {"traceparent": "00-abc-def-01"}


def test_help_without_optional_context_still_accepts_an_operator_reply() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.HELP_NEEDED,
            {"reason": "uncertain", "summary": "I need clarification"},
        )
    )[0]

    assert projected["replyContext"]["session_id"] == "session-1"
    assert projected["replyContext"]["correlation_id"] == "corr-1"
    assert projected["replyContext"]["help_context"] == {}


def test_tool_projection_exposes_activity_and_delegation_without_skuld_logic() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.TOOL_START,
            {
                "tool_name": "route_work",
                "input": {"event_type": "code.review", "prompt": "Review it"},
            },
        ),
        persona="Coder",
    )

    assert [event["kind"] for event in projected] == [
        "activity",
        "delegation",
        "agent_event",
    ]
    assert projected[1]["eventType"] == "code.review"


def test_outcome_projection_is_structured_and_instruction_free() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "printer.release_overcount",
                "fields": {"observed": 4, "expected": 3},
            },
        )
    )[0]

    assert projected["kind"] == "outcome"
    assert projected["eventType"] == "printer.release_overcount"
    assert projected["fields"] == {"observed": 4, "expected": 3}
    assert "instruction" not in projected


def test_outcome_already_sent_to_skuld_is_routing_only_on_mesh() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "review.completed",
                "verdict": "approved",
                "room_bridge_skip": True,
            },
        )
    )[0]

    assert projected["routingOnly"] is True
