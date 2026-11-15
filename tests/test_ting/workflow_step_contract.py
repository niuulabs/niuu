"""The wire contract between Ting, the run's root and the members' Skuld, as fixtures.

These are the JSON shapes of the contract verbatim, so a drift in either side shows up as
a failing test on the other. Ting hands a signed offer to the run's root
(``POST .../sessions/{root}/room`` with operation ``offer``); the root publishes it on the
mesh; each member's Skuld reports what it did with it as session activity metadata
(``metadata.workflow_step``), which Ting records. The field names are the ones
``skuld.workflow_steps`` writes.
"""

from __future__ import annotations

RUN_ID = "5f0f9a52-33e0-4f79-9d6e-3e7a2f6fd0a1"
OFFER_ID = "2d7c4e18-6a3b-4f0c-8e55-91b0c7d3a2f4"
ACTIVATION_ID = "0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10"
MEMBER = "coder"
START_ID = "start-1"

#: ``niuu.domain.workflow_step.StepOffered.to_payload()`` of an offer to one member.
OFFERED_PAYLOAD = {
    "kind": "step.offered",
    "run_id": RUN_ID,
    "offer_id": OFFER_ID,
    "node_id": "implement",
    "node_label": "Implement",
    "workflow_name": "Build",
    "topic": "code.requested",
    "attempt": 1,
    "generation": 0,
    "inputs": [
        {
            "seq": 1,
            "event_type": "code.requested",
            "edge_id": "e1",
            "node_id": "plan",
            "member": "planner",
            "payload": {"verdict": "planned", "note": "split into two changes"},
        }
    ],
    "deadline_at": "2026-11-08T12:00:00Z",
    "instructions": "full text Ting rendered for this offer",
    "contracts": [
        {
            "persona": {
                "alias": MEMBER,
                "id": "coder",
                "revision": "rev",
                "digest": "sha256:" + "0" * 64,
            },
            "valid_endings": ["verdict", "waiting", "done", "failed"],
            "valid_verdicts": ["implemented", "blocked"],
            "verdict_events": {"implemented": "code.ready", "blocked": "work.blocked"},
            "default_event": "",
            "outcome_schema": {"summary": {"type": "string", "description": "what changed"}},
            "verdict_when": {},
        }
    ],
}

#: The body Ting hands the root's ``offer`` operation: the offer and Ting's signature on it.
OFFER_BODY = {
    "offer": OFFERED_PAYLOAD,
    "attestation": {"v": 1, "cert": "jwt-of-service:ting", "at": 1762596000000, "sig": "c2ln"},
}

#: Keys the offer body must carry, whatever their values.
OFFER_BODY_KEYS = frozenset(OFFER_BODY)

#: Keys the offered payload must carry, whatever their values.
OFFERED_KEYS = frozenset(OFFERED_PAYLOAD)

#: A member's Skuld reports that its mesh subscriptions are up.
LISTENING_METADATA = {
    "workflow_step": {
        "event": "listening",
        "run_id": RUN_ID,
        "member": MEMBER,
        "start_id": START_ID,
        "topics": ["code.requested"],
    }
}

#: The root's Skuld reports that it published the offer on the mesh.
OFFER_PUBLISHED_METADATA = {
    "workflow_step": {
        "event": "offer_published",
        "run_id": RUN_ID,
        "offer_id": OFFER_ID,
        "node_id": "implement",
        "topic": "code.requested",
    }
}

#: A member took the offer up: ``activation_id`` is the member's own, minted from the offer.
PICKED_UP_METADATA = {
    "workflow_step": {
        "event": "picked_up",
        "run_id": RUN_ID,
        "offer_id": OFFER_ID,
        "node_id": "implement",
        "member": MEMBER,
        "activation_id": ACTIVATION_ID,
        "attempt": 1,
    }
}

#: A member passed the offer: it is not its to take.
PASSED_METADATA = {
    "workflow_step": {
        "event": "passed",
        "run_id": RUN_ID,
        "offer_id": OFFER_ID,
        "node_id": "implement",
        "member": MEMBER,
        "reason": "filters",
    }
}

#: A member could neither take nor pass the offer, and says why.
OFFER_REFUSED_METADATA = {
    "workflow_step": {
        "event": "offer_refused",
        "run_id": RUN_ID,
        "offer_id": OFFER_ID,
        "node_id": "implement",
        "member": MEMBER,
        "reason": "busy",
        "detail": "the member holds another step",
    }
}

#: The activity metadata Skuld reports when a step was closed with a verdict.
CLOSED_METADATA = {
    "workflow_step": {
        "event": "closed",
        "run_id": RUN_ID,
        "offer_id": OFFER_ID,
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
        "offer_id": OFFER_ID,
        "activation_id": ACTIVATION_ID,
        "attempt": 1,
    }
}

#: Every report a member's Skuld makes, by its ``event``.
REPORTS = {
    "listening": LISTENING_METADATA,
    "offer_published": OFFER_PUBLISHED_METADATA,
    "picked_up": PICKED_UP_METADATA,
    "passed": PASSED_METADATA,
    "offer_refused": OFFER_REFUSED_METADATA,
    "closed": CLOSED_METADATA,
    "unclosed": UNCLOSED_METADATA,
}
