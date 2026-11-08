"""The phase 2 wire contract between Ting and Forge/Skuld, as fixtures.

These are the JSON shapes of the contract verbatim, so a drift in either side shows up as
a failing test on the other. Forge's session create takes the pinned persona document and
the step-host marker; Forge's step route takes the activation body; Skuld reports a close
(or an unclosed turn) as session activity metadata.
"""

from __future__ import annotations

RUN_ID = "5f0f9a52-33e0-4f79-9d6e-3e7a2f6fd0a1"
ACTIVATION_ID = "0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10"

#: The body Ting delivers to ``POST /api/v1/forge/sessions/{id}/workflow/steps``.
ACTIVATION_BODY = {
    "activation_id": ACTIVATION_ID,
    "run_id": RUN_ID,
    "attempt": 1,
    "generation": 0,
    "node_id": "implement",
    "node_label": "Implement",
    "member": "coder",
    "valid_endings": ["verdict", "waiting", "done", "failed"],
    "valid_verdicts": ["implemented", "blocked"],
    "outcome_schema": {"summary": {"type": "string", "description": "what changed"}},
    "deadline_at": "2026-11-08T12:00:00+00:00",
    "instructions": "full text Ting rendered for this step",
}

#: Keys the activation body must carry, whatever their values.
ACTIVATION_KEYS = frozenset(ACTIVATION_BODY)

#: The activity metadata Skuld reports when a step was closed with a verdict.
CLOSED_METADATA = {
    "workflow_step": {
        "event": "closed",
        "run_id": RUN_ID,
        "activation_id": ACTIVATION_ID,
        "attempt": 1,
        "ending": {
            "kind": "verdict",
            "verdict": "implemented",
            "outcome": {"summary": "added the endpoint"},
            "note": "tests pass",
            "evidence": [{"kind": "commit", "ref": "abc123", "summary": "the change"}],
            "waiting_on": None,
            "failure_reason": None,
        },
    }
}

#: The activity metadata Skuld reports when a turn ended with the step still open.
UNCLOSED_METADATA = {
    "workflow_step": {
        "event": "unclosed",
        "run_id": RUN_ID,
        "activation_id": ACTIVATION_ID,
        "attempt": 1,
    }
}
