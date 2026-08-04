"""Contracts for resident Valkyrie outcome publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

VALKYRIE_JUDGMENT_PROPOSED = "valkyrie.judgment.proposed"
VALKYRIE_JUDGMENT_REJECTED = "valkyrie.judgment.rejected"
VALKYRIE_STATE_UPDATED = "valkyrie.state.updated"
VALKYRIE_ACTION_PROPOSED = "valkyrie.action.proposed"
VALKYRIE_ACTION_EXECUTED = "valkyrie.action.executed"
VALKYRIE_ACTION_FAILED = "valkyrie.action.failed"

# Ordered rather than set-literal because these tuples are also what the model
# is shown, and the order carries meaning: attention tiers and wakefulness
# states escalate left to right. The frozensets used for validation are derived
# from them so the prompt cannot describe a vocabulary the validator rejects.
VALKYRIE_ATTENTION_TIER_ORDER = ("silent", "ambient", "present", "urgent")
VALKYRIE_WAKEFULNESS_STATE_ORDER = ("sleeping", "watching", "wakeful", "dreaming")
VALKYRIE_ACTION_AUTHORITY_ORDER = (
    "autonomous",
    "yolo_allowed",
    "court_required",
    "human_review_required",
)
VALKYRIE_DECISION_ORDER = (
    "ignore",
    "watch",
    "investigate",
    "propose_action",
    "escalate",
    "learn",
    "blocked",
)
VALKYRIE_OPERATIONAL_STATE_ORDER = (
    "nominal",
    "watching",
    "investigating",
    "degraded",
    "remediating",
    "blocked",
    "dreaming",
)
VALKYRIE_CONTINUATION_ORDER = ("ask_operator", "sleep", "stop")
VALKYRIE_NEXT_ACTION_TIMING_ORDER = (
    "external_event",
    "scheduled_time",
    "operator_input",
    "none",
)

VALKYRIE_ATTENTION_TIERS = frozenset(VALKYRIE_ATTENTION_TIER_ORDER)
VALKYRIE_WAKEFULNESS_STATES = frozenset(VALKYRIE_WAKEFULNESS_STATE_ORDER)
VALKYRIE_ACTION_AUTHORITIES = frozenset(VALKYRIE_ACTION_AUTHORITY_ORDER)
VALKYRIE_OUTCOME_EVENTS = frozenset(
    {
        VALKYRIE_JUDGMENT_PROPOSED,
        VALKYRIE_STATE_UPDATED,
        VALKYRIE_ACTION_PROPOSED,
        VALKYRIE_ACTION_EXECUTED,
        VALKYRIE_ACTION_FAILED,
    }
)
VALKYRIE_RUNTIME_OWNED_FIELDS = frozenset(
    {"environment_id", "environment_type", "valkyrie_id", "correlation_ids"}
)

_ACTION_AUTHORITY_ALIASES = {
    "court": "court_required",
    "human_review": "human_review_required",
    "human": "human_review_required",
    "review": "human_review_required",
    "yolo": "yolo_allowed",
}
_OPERATIONAL_STATE_ALIASES = {
    "investigate": "investigating",
}
_WAKEFULNESS_ALIASES = {
    "watchful": "watching",
}

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


def _choices(options: Sequence[str]) -> str:
    return f"<{' | '.join(options)}>"


def _working_state_lines() -> list[str]:
    """Render the durable snapshot skeleton the continuation contract requires.

    Imported here rather than hardcoded so the shown field names cannot drift
    from the ones ``validate_resident_working_state`` insists on.
    """
    from ravn.domain.resident_continuation import (
        RESIDENT_WORKING_STATE_FIELDS,
        RESIDENT_WORKING_STATE_MAX_ENTRIES,
    )

    lines = [
        f"  # each list holds at most {RESIDENT_WORKING_STATE_MAX_ENTRIES} short "
        "strings or mappings; use [] when you have none"
    ]
    lines.extend(f"  {field}: []" for field in RESIDENT_WORKING_STATE_FIELDS)
    return lines


def resident_outcome_template(
    *,
    signal_refs: Sequence[str] = (),
    evidence_lines: Sequence[str] = (),
) -> str:
    """Render the literal outcome block a resident turn must reproduce.

    Every resident turn is validated against this contract, so every resident
    turn has to be shown it. Signal-driven turns pass the refs and evidence they
    already know; charter-driven wakes (stewardship, scheduled wake, operator
    answer, dream cycle) get placeholders for the same fields.
    """
    ref_lines = [f"  - {ref}" for ref in signal_refs] or [
        "  - <inbox or continuation ref you actually read, or leave the list empty>"
    ]
    ev_lines = list(evidence_lines) or [
        "  - <what you observed, with its source; leave the list empty if you gathered none>"
    ]
    return "\n".join(
        [
            "---outcome---",
            f"decision: {_choices(VALKYRIE_DECISION_ORDER)}",
            "signal_refs:",
            *ref_lines,
            f"tier: {_choices(VALKYRIE_ATTENTION_TIER_ORDER)}",
            "confidence: <0.0-1.0>",
            f"operational_state: {_choices(VALKYRIE_OPERATIONAL_STATE_ORDER)}",
            f"wakefulness: {_choices(VALKYRIE_WAKEFULNESS_STATE_ORDER)}",
            "rationale: concise reason grounded in what you observed",
            "evidence:",
            *ev_lines,
            "recommended_action: <next step, or none>",
            "selected_next_action: <one concrete next step, or none>",
            f"continuation: {_choices(VALKYRIE_CONTINUATION_ORDER)}",
            f"next_action_timing: {_choices(VALKYRIE_NEXT_ACTION_TIMING_ORDER)}",
            'question: ""',
            f"action_authority: {_choices(VALKYRIE_ACTION_AUTHORITY_ORDER)}",
            "action_capability: <required capability, or none>",
            "target_surfaces: []",
            'expires_at: ""',
            "dissent_refs: []",
            "state_summary: <one line the operator could read as your current state>",
            "working_state:",
            *_working_state_lines(),
            "---end---",
        ]
    )


def resident_outcome_section(
    *,
    signal_refs: Sequence[str] = (),
    evidence_lines: Sequence[str] = (),
) -> str:
    """Render the '## Required outcome' prompt section around the template."""
    template = resident_outcome_template(signal_refs=signal_refs, evidence_lines=evidence_lines)
    return (
        "## Required outcome\n\n"
        "Finish with exactly one `valkyrie.judgment.proposed` block in this "
        "shape — keep the `---outcome---` / `---end---` delimiters, valid YAML "
        "between them, and no prose after it:\n\n"
        "```text\n"
        f"{template}\n"
        "```\n"
    )


def is_valkyrie_outcome_event(event_type: str) -> bool:
    """Return whether *event_type* is a resident Valkyrie outcome contract."""
    return event_type in VALKYRIE_OUTCOME_EVENTS


def normalize_valkyrie_outcome(event_type: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return resident Valkyrie fields normalized for validation and transport.

    Local models can be good operators while still being loose YAML emitters:
    unquoted timestamps become ``datetime`` objects, single refs sometimes arrive
    as scalars, and wakefulness vocabulary may drift between UI and persona
    wording.  Normalize those edge cases at the contract boundary without
    inventing missing core judgment data.
    """
    normalized = {
        str(key): _json_safe_value(value) for key, value in fields.items() if str(key).strip()
    }
    if event_type != VALKYRIE_JUDGMENT_PROPOSED:
        return normalized

    authority = str(normalized.get("action_authority", "") or "").strip()
    if authority:
        normalized["action_authority"] = _ACTION_AUTHORITY_ALIASES.get(authority, authority)

    operational_state = str(normalized.get("operational_state", "") or "").strip()
    if operational_state:
        normalized["operational_state"] = _OPERATIONAL_STATE_ALIASES.get(
            operational_state.lower(),
            operational_state,
        )

    wakefulness = str(normalized.get("wakefulness", "") or "").strip()
    if wakefulness:
        normalized["wakefulness"] = _WAKEFULNESS_ALIASES.get(wakefulness.lower(), wakefulness)

    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        confidence = _strip_wrapping_quotes(confidence.strip())
        try:
            normalized["confidence"] = float(confidence.strip())
        except ValueError:
            # Invalid confidence strings are left as-is for downstream validation.
            pass

    expires_at = normalized.get("expires_at")
    if expires_at is None:
        normalized["expires_at"] = ""
    elif "expires_at" in normalized:
        normalized["expires_at"] = str(expires_at)

    for array_field in ("signal_refs", "evidence", "target_surfaces", "dissent_refs"):
        if array_field not in normalized:
            continue
        value = normalized[array_field]
        if array_field == "dissent_refs" and (
            value is None or str(value).strip().lower() in {"null", "none"}
        ):
            normalized[array_field] = []
        elif array_field == "evidence" and not isinstance(value, list):
            if isinstance(value, Mapping):
                normalized[array_field] = [dict(value)]
            elif not str(value or "").strip():
                flattened_evidence = {
                    key: normalized[key]
                    for key in ("event_id", "kind", "object", "namespace", "reason", "message")
                    if str(normalized.get(key, "") or "").strip()
                }
                normalized[array_field] = [flattened_evidence] if flattened_evidence else []
            else:
                normalized[array_field] = [value]
        elif not isinstance(value, list):
            normalized[array_field] = [value]

    correlation_ids = normalized.get("correlation_ids")
    if correlation_ids is None and "correlation_ids" in normalized:
        normalized["correlation_ids"] = {}
    elif "correlation_ids" in normalized and not isinstance(correlation_ids, Mapping):
        flattened_correlation = {
            key: normalized[key]
            for key in ("root", "task", "signal", "environment")
            if str(normalized.get(key, "") or "").strip()
        }
        if flattened_correlation:
            normalized["correlation_ids"] = flattened_correlation
        elif isinstance(correlation_ids, list):
            normalized["correlation_ids"] = {"refs": correlation_ids} if correlation_ids else {}
        elif str(correlation_ids or "").strip():
            normalized["correlation_ids"] = {"root": str(correlation_ids).strip()}
        else:
            normalized["correlation_ids"] = {}

    if not str(normalized.get("state_summary", "") or "").strip():
        operational_state = str(normalized.get("operational_state", "") or "").strip()
        rationale = str(normalized.get("rationale", "") or "").strip()
        if operational_state or rationale:
            normalized["state_summary"] = ": ".join(
                part for part in (operational_state, rationale) if part
            )

    return normalized


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _json_safe_value(value: Any) -> Any:
    """Coerce values commonly produced by YAML parsing into wire-safe objects."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


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

    wakefulness = str(fields.get("wakefulness", "") or "").strip()
    if wakefulness and wakefulness not in VALKYRIE_WAKEFULNESS_STATES:
        allowed = ", ".join(sorted(VALKYRIE_WAKEFULNESS_STATES))
        errors.append(
            f"resident judgment wakefulness {wakefulness!r} is invalid; expected one of {allowed}"
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
