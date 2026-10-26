"""Reading the reason out of an A2A task status."""

from __future__ import annotations

import pytest

from ravn.domain.a2a_status import a2a_status_text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            {
                "state": "TASK_STATE_FAILED",
                "message": {
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "first"}, {"data": {}}, {"text": " second "}],
                },
            },
            "first\nsecond",
        ),
        ({"state": "TASK_STATE_WORKING", "message": "plain words"}, "plain words"),
        ({"state": "TASK_STATE_WORKING", "update": "legacy update"}, "legacy update"),
        ({"state": "TASK_STATE_WORKING", "message": {"parts": None}}, ""),
        ({"state": "TASK_STATE_WORKING"}, ""),
        (None, ""),
    ],
)
def test_status_text_reads_message_parts(status, expected) -> None:
    assert a2a_status_text(status) == expected
