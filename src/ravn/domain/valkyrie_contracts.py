"""Contracts for resident Valkyrie outcome publication."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VALKYRIE_JUDGMENT_PROPOSED = "valkyrie.judgment.proposed"
VALKYRIE_JUDGMENT_REJECTED = "valkyrie.judgment.rejected"
VALKYRIE_STATE_UPDATED = "valkyrie.state.updated"
VALKYRIE_ACTION_PROPOSED = "valkyrie.action.proposed"
VALKYRIE_ACTION_EXECUTED = "valkyrie.action.executed"
VALKYRIE_ACTION_FAILED = "valkyrie.action.failed"

VALKYRIE_ATTENTION_TIERS = frozenset({"silent", "ambient", "present", "urgent"})
VALKYRIE_ACTION_AUTHORITIES = frozenset(
    {"autonomous", "yolo_allowed", "court_required", "human_review_required"}
)
VALKYRIE_OUTCOME_EVENTS = frozenset(
    {
        VALKYRIE_JUDGMENT_PROPOSED,
        VALKYRIE_STATE_UPDATED,
        VALKYRIE_ACTION_PROPOSED,
        VALKYRIE_ACTION_EXECUTED,
        VALKYRIE_ACTION_FAILED,
    }
)

_JUDGMENT_REQUIRED_FIELDS = (
    "environment_id",
    "valkyrie_id",
    "signal_refs",
    "tier",
    "confidence",
    "operational_state",
    "rationale",
    "evidence",
    "recommended_action",
    "action_authority",
    "target_surfaces",
    "expires_at",
    "dissent_refs",
    "correlation_ids",
)


def is_valkyrie_outcome_event(event_type: str) -> bool:
    """Return whether *event_type* is a resident Valkyrie outcome contract."""
    return event_type in VALKYRIE_OUTCOME_EVENTS


def validate_valkyrie_outcome(event_type: str, fields: Mapping[str, Any]) -> list[str]:
    """Return validation errors for a resident Valkyrie outcome payload."""
    if event_type != VALKYRIE_JUDGMENT_PROPOSED:
        return []

    errors: list[str] = []
    for field in _JUDGMENT_REQUIRED_FIELDS:
        if field not in fields:
            errors.append(f"resident judgment requires field '{field}'")

    tier = str(fields.get("tier", "") or "").strip()
    if tier and tier not in VALKYRIE_ATTENTION_TIERS:
        allowed = ", ".join(sorted(VALKYRIE_ATTENTION_TIERS))
        errors.append(f"resident judgment tier {tier!r} is invalid; expected one of {allowed}")

    authority = str(fields.get("action_authority", "") or "").strip()
    if authority and authority not in VALKYRIE_ACTION_AUTHORITIES:
        allowed = ", ".join(sorted(VALKYRIE_ACTION_AUTHORITIES))
        errors.append(
            f"resident judgment action_authority {authority!r} is invalid; "
            f"expected one of {allowed}"
        )

    confidence = fields.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        errors.append("resident judgment confidence must be a number")
    elif not 0.0 <= float(confidence) <= 1.0:
        errors.append("resident judgment confidence must be between 0.0 and 1.0")

    for array_field in ("signal_refs", "evidence", "target_surfaces", "dissent_refs"):
        if array_field in fields and not isinstance(fields[array_field], list):
            errors.append(f"resident judgment {array_field} must be an array")

    if "correlation_ids" in fields and not isinstance(fields["correlation_ids"], dict):
        errors.append("resident judgment correlation_ids must be an object")

    return errors
