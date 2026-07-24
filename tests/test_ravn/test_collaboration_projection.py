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
    assert projected["sourceEventId"]
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


def test_error_projection_preserves_failure_kind() -> None:
    projected = project_ravn_event(
        RavnEvent.error(
            source="ravn-1",
            message="Backend unavailable",
            correlation_id="task-1",
            session_id="session-1",
            failure_kind="LLMError",
        )
    )[0]

    assert projected["failureKind"] == "LLMError"


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
    assert len({event["sourceEventId"] for event in projected}) == 1


def test_outcome_projection_is_structured_and_instruction_free() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "printer.release_overcount",
                "fields": {"observed": 4, "expected": 3},
                "workflow_parent_event_id": "activation-1",
            },
        )
    )[0]

    assert projected["kind"] == "outcome"
    assert projected["eventType"] == "printer.release_overcount"
    assert projected["fields"] == {"observed": 4, "expected": 3}
    assert projected["context"] == {"workflow_parent_event_id": "activation-1"}
    assert "instruction" not in projected


def test_collaboration_delivery_marker_does_not_hide_direct_outcome() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "review.completed",
                "verdict": "approved",
                "collaboration_routing_only": True,
            },
        )
    )[0]

    assert "routingOnly" not in projected
    assert "context" not in projected
    assert "collaboration_routing_only" not in projected["fields"]


def test_routing_only_outcome_is_explicit_in_collaboration_contract() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "review.changes_requested",
                "routing_only": True,
            },
        )
    )[0]

    assert projected["routingOnly"] is True
