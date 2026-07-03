"""Project Valkyrie bus events into durable history records.

Judgments, actions, and signals live on the Sleipnir bus as ephemeral
events. These pure functions turn one event dict into the flat record a
:class:`ravn.ports.valkyrie_history.ValkyrieHistoryStore` persists, so the
dashboard can answer "which decision, why, and what happened next" after
the projection process restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sleipnir.domain import registry

#: Judgment authorities that must land in the operator review inbox.
#: ``court_required`` is deliberately absent: the ODIN court subscribes to
#: those judgments itself and files a ``draft_for_review`` escalation when
#: one is warranted — central filing would duplicate every court case.
REVIEW_REQUIRED_AUTHORITIES = frozenset({"human_review_required"})

#: Attention tiers that mean "a human should look at this". Guarded
#: residents stamp human_review_required on every judgment — including
#: ambient "used a learned skill, all fine" telemetry — so authority alone
#: is not consent to page the operator.
_REVIEW_ATTENTION_TIERS = frozenset({"present", "urgent"})

#: Recommendations that describe observation, not an action to approve.
_OBSERVATIONAL_ACTIONS = frozenset({"", "none", "n/a", "watch", "observe"})

_ACTION_EVENT_TYPES = frozenset(
    {
        registry.VALKYRIE_ACTION_PROPOSED,
        registry.VALKYRIE_ACTION_REQUESTED,
        registry.VALKYRIE_ACTION_COMPLETED,
        registry.VALKYRIE_ACTION_EXECUTED,
        registry.VALKYRIE_ACTION_FAILED,
    }
)

_ACTION_STATUS_BY_EVENT = {
    registry.VALKYRIE_ACTION_PROPOSED: "proposed",
    registry.VALKYRIE_ACTION_REQUESTED: "requested",
    registry.VALKYRIE_ACTION_COMPLETED: "executed",
    registry.VALKYRIE_ACTION_EXECUTED: "executed",
    registry.VALKYRIE_ACTION_FAILED: "failed",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(event: dict[str, Any]) -> str:
    value = event.get("timestamp")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return _now_iso()


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _details(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("fields", "outcome"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return payload


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(entry) for entry in value if str(entry).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def decision_record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a decision record for a judgment event; None for anything else."""
    if str(event.get("event_type") or "") != registry.VALKYRIE_JUDGMENT_PROPOSED:
        return None
    payload = _payload(event)
    details = _details(payload)
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return None
    evidence = details.get("evidence")
    return {
        "decisionId": event_id,
        "environmentId": str(
            details.get("environment_id") or payload.get("environment_id") or "unknown"
        ),
        "valkyrieId": str(details.get("valkyrie_id") or payload.get("valkyrie_id") or ""),
        "operationalState": str(details.get("operational_state") or "watching"),
        "tier": str(details.get("tier") or "ambient"),
        "wakefulness": str(details.get("wakefulness") or ""),
        "confidence": _float(details.get("confidence")),
        "rationale": str(details.get("rationale") or event.get("summary") or ""),
        "recommendedAction": str(details.get("recommended_action") or ""),
        "actionAuthority": str(details.get("action_authority") or "autonomous"),
        "actionCapability": str(details.get("action_capability") or ""),
        "signalRefs": _string_list(details.get("signal_refs")),
        "evidence": evidence if isinstance(evidence, list) else [],
        "correlationId": str(event.get("correlation_id") or ""),
        "summary": str(event.get("summary") or ""),
        "source": str(event.get("source") or ""),
        "outcome": "",
        "outcomeDetail": "",
        "outcomeAt": "",
        "reviewItemId": "",
        "decidedAt": _timestamp(event),
    }


def action_record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return an action record for any valkyrie.action.* event; None otherwise."""
    event_type = str(event.get("event_type") or "")
    if event_type not in _ACTION_EVENT_TYPES:
        return None
    payload = _payload(event)
    details = _details(payload)
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return None
    action_id = str(details.get("action_id") or event_id)
    return {
        "actionId": action_id,
        "eventId": event_id,
        "eventType": event_type,
        "status": _ACTION_STATUS_BY_EVENT[event_type],
        "environmentId": str(
            details.get("environment_id") or payload.get("environment_id") or "unknown"
        ),
        "valkyrieId": str(details.get("valkyrie_id") or payload.get("valkyrie_id") or ""),
        "capability": str(details.get("capability") or ""),
        "actionAuthority": str(
            details.get("action_authority") or details.get("authority_boundary") or ""
        ),
        "outcome": str(details.get("outcome") or ""),
        "rationale": str(details.get("rationale") or ""),
        "dryRun": bool(details.get("dry_run", False)),
        "correlationId": str(event.get("correlation_id") or ""),
        "summary": str(event.get("summary") or ""),
        "observedAt": _timestamp(event),
    }


def signal_record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a signal record for a signal.* event; None otherwise."""
    event_type = str(event.get("event_type") or "")
    if not event_type.startswith("signal."):
        return None
    payload = _payload(event)
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return None
    severity = str(payload.get("severity") or payload.get("level") or "info").lower()
    subject = str(
        payload.get("subject")
        or payload.get("signal_id")
        or payload.get("resource")
        or payload.get("object")
        or ""
    )
    return {
        "signalId": event_id,
        "environmentId": str(payload.get("environment_id") or event.get("tenant_id") or "unknown"),
        "eventType": event_type,
        "source": str(event.get("source") or event_type),
        "subject": subject or event_type,
        "summary": str(event.get("summary") or payload.get("summary") or event_type),
        "severity": severity,
        "correlationId": str(event.get("correlation_id") or ""),
        "receivedAt": _timestamp(event),
    }


def decision_requires_review(record: dict[str, Any]) -> bool:
    """True when a stored decision must be routed to the operator inbox.

    Three things must hold: the authority demands a human, the judgment is
    pitched at an attention tier that asks for one, and it recommends an
    actual action rather than continued observation. Anything less is
    telemetry, not a decision awaiting approval.
    """
    if str(record.get("actionAuthority") or "") not in REVIEW_REQUIRED_AUTHORITIES:
        return False
    if str(record.get("tier") or "").lower() not in _REVIEW_ATTENTION_TIERS:
        return False
    recommended = str(record.get("recommendedAction") or "").strip().lower()
    return recommended not in _OBSERVATIONAL_ACTIONS
