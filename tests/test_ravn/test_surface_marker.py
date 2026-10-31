"""The escalation marker steers Ravn and never reaches a person."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ravn.adapters.channels.silent import SilentChannel
from ravn.adapters.collaboration import project_ravn_event
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.surface import strip_surface_marker


def _event(event_type: RavnEventType, payload: dict) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="ravn",
        payload=payload,
        timestamp=datetime.now(UTC),
        urgency=0.5,
        correlation_id="c",
        session_id="s",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[SURFACE] The gate is open.", "The gate is open."),
        ("  [SURFACE]The gate is open.", "The gate is open."),
        ("Summary\n[SURFACE] Needs you", "Summary\nNeeds you"),
        ("Mentions [SURFACE] in passing", "Mentions [SURFACE] in passing"),
        ("Nothing to strip", "Nothing to strip"),
    ],
)
def test_the_marker_comes_off_the_start_of_a_line(text: str, expected: str) -> None:
    assert strip_surface_marker(text) == expected


def test_a_room_message_never_shows_the_marker() -> None:
    projected = project_ravn_event(
        _event(RavnEventType.RESPONSE, {"text": "[SURFACE] The gate is open."})
    )

    assert projected[0]["content"] == "The gate is open."


def test_an_outcome_summary_never_shows_the_marker() -> None:
    projected = project_ravn_event(
        _event(
            RavnEventType.OUTCOME,
            {
                "event_type": "code.changed",
                "summary": "[SURFACE] Changed the README",
                "fields": {"summary": "[SURFACE] Changed the README"},
                "outcome": {"summary": "[SURFACE] Changed the README"},
            },
        ),
        persona="coder",
    )

    outcome = projected[0]
    assert outcome["summary"] == "Changed the README"
    assert outcome["fields"]["summary"] == "Changed the README"


async def test_ravn_still_sees_the_marker_so_escalation_keeps_working() -> None:
    channel = SilentChannel()

    await channel.emit(_event(RavnEventType.RESPONSE, {"text": "[SURFACE] Needs a person"}))

    assert channel.surface_triggered is True
