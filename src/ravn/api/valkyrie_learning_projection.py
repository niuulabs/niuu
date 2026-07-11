"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from ravn.api.valkyrie_config import (
    ValkyrieDashboardConfig,
    configured_environment_records,
)
from ravn.api.valkyrie_requests import (
    AutonomyUpdateRequest,
    HuddleJoinRequest,
    HuddleSendRequest,
    LearningDecisionRequest,
    LearningFeedbackRequest,
    LearningReviseRequest,
)
from ravn.domain.valkyrie_history import canonical_environment_id
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent

Dashboard = dict[str, Any]
RAW_SIGNAL_TELEMETRY_LIMIT = 1_000
CONTROL_TELEMETRY_LIMIT = 2_000
LEARNING_SCOPES = ("private", "environment", "domain", "flock", "shared")
from ravn.api.valkyrie_projection_common import (  # noqa: F401
    _now,
    _as_int,
    _as_float,
    _as_string_list,
    _field,
    _slug,
    _environment_id,
    _canonical_environment_id,
    _valkyrie_id,
    _rollup_health,
    _first_transport_value,
    _live_report,
    _empty_telemetry,
)

from ravn.api.valkyrie_event_projection import (  # noqa: F401
    _event_dict,
    _is_raw_signal_event,
    _is_runtime_event,
    _event_timestamp,
    _payload_int,
    _payload_float,
    _event_environment_id,
    _event_valkyrie_id,
    _event_valkyrie_name,
    _event_kind,
    _event_tier,
    _event_log_entry,
    _signal_severity,
    _signal_subject,
    _signal_entry,
    _signals_from_events,
    _state_drift,
    _operational_state_entry,
    _operational_states_from_events,
    _court_decision_status,
    _court_decision_risk,
    _court_decision_entry,
    _court_decisions_from_events,
    _structured_log_entry,
)


def _learning_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    status: str = "",
) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    details = fields or payload
    gap = details.get("gap") if isinstance(details.get("gap"), dict) else {}
    evidence = gap.get("evidence") if isinstance(gap.get("evidence"), dict) else {}
    source_signal_ids = _as_string_list(
        gap.get("signal_ids") or payload.get("signal_ids") or payload.get("signal_id") or ""
    )
    if not source_signal_ids and payload.get("signal_id"):
        source_signal_ids = [str(payload.get("signal_id"))]
    artifact_content = str(
        details.get("skill_content")
        or payload.get("skill_content")
        or payload.get("artifact_content")
        or payload.get("content")
        or ""
    )
    review_payload = {}
    if event_type == "odin.court.decided":
        dissent = payload.get("dissent") if isinstance(payload.get("dissent"), list) else []
        review_detail = next(
            (item for item in dissent if isinstance(item, dict) and item.get("reviewer")),
            {},
        )
        review_payload = {
            "outcome": str(payload.get("outcome") or review_detail.get("outcome") or ""),
            "approved": bool(payload.get("approved") or review_detail.get("approved")),
            "rationale": str(payload.get("rationale") or review_detail.get("rationale") or ""),
            "reviewer": str(payload.get("reviewer") or review_detail.get("reviewer") or ""),
            "findings": _as_string_list(
                payload.get("findings") or review_detail.get("findings") or []
            ),
            "requiredForActivation": bool(payload.get("required_for_activation")),
        }
    return {
        "id": str(
            payload.get("proposal_id")
            or payload.get("learning_id")
            or payload.get("dream_id")
            or payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or details.get("request_id")
            or event.get("event_id")
            or f"{event_type}:{_event_timestamp(event)}"
        ),
        "eventType": event_type,
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload) or str(gap.get("source_valkyrie_id") or ""),
        "dreamId": str(payload.get("dream_id") or ""),
        "title": str(
            details.get("title")
            or payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or gap.get("capability_name")
            or details.get("artifact_type")
            or event.get("summary")
            or event_type
        ),
        "status": status or str(details.get("status") or event_type.rsplit(".", 1)[-1]),
        "artifactType": str(details.get("artifact_type") or payload.get("artifact_type") or ""),
        "riskClass": str(
            details.get("risk_class")
            or details.get("negative_transfer_risk")
            or payload.get("negative_transfer_risk")
            or "low"
        ),
        "policyDecision": str(details.get("policy_decision") or ""),
        "scope": str(
            details.get("target_scope")
            or payload.get("to_scope")
            or payload.get("scope")
            or "flock"
        ),
        "sourceEnvironmentId": str(
            gap.get("environment_id") or _event_environment_id(event, payload)
        ),
        "sourceValkyrieId": str(
            payload.get("source_valkyrie_id")
            or gap.get("source_valkyrie_id")
            or _event_valkyrie_id(payload)
            or "valkyrie:local"
        ),
        "targetFlockId": str(payload.get("flock_id") or details.get("flock_id") or "flock-local"),
        "confidence": _payload_float(details, "confidence")
        or _payload_float(payload, "confidence"),
        "evaluation": str(
            details.get("evaluation")
            or payload.get("rationale")
            or payload.get("summary")
            or evidence.get("summary")
            or event.get("summary")
            or ""
        ),
        "negativeTransferRisk": str(
            details.get("negative_transfer_risk") or payload.get("negative_transfer_risk") or "low"
        ),
        "redaction": str(
            details.get("redaction")
            or payload.get("redaction")
            or payload.get("redaction_status")
            or "none"
        ),
        "promotedTool": str(
            payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or details.get("artifact_name")
            or ""
        ),
        "proposalsCreated": _payload_int(payload, "proposals_created"),
        "proposalsApplied": _payload_int(payload, "proposals_applied"),
        "proposalsDeferred": _payload_int(payload, "proposals_deferred"),
        "observedAt": _event_timestamp(event),
        "summary": str(event.get("summary") or ""),
        "artifactContent": artifact_content,
        "artifactPath": str(details.get("artifact_path") or payload.get("artifact_path") or ""),
        "sourceSignalIds": source_signal_ids,
        "sourceEvidence": evidence,
        "dreamRationale": str(gap.get("reason") or payload.get("rationale") or ""),
        "odinReview": review_payload,
        "feedback": (
            dict(payload["feedback"]) if isinstance(payload.get("feedback"), dict) else None
        ),
        "repetition": _payload_int(payload, "repetition")
        or _payload_int(details, "repetition")
        or 1,
        "supersedes": str(details.get("supersedes") or payload.get("supersedes") or ""),
        "history": [
            {
                "eventType": event_type,
                "status": status or str(details.get("status") or event_type.rsplit(".", 1)[-1]),
                "summary": str(event.get("summary") or ""),
                "observedAt": _event_timestamp(event),
                "residentValkyrieId": str(payload.get("resident_valkyrie_id") or ""),
                "residentValkyrieName": str(payload.get("resident_valkyrie_name") or ""),
                "action": str(payload.get("action") or ""),
                "relevant": payload.get("relevant"),
                "installedSkillName": str(payload.get("installed_skill_name") or ""),
                "odinDecision": str(payload.get("decision") or ""),
            }
        ],
    }


def _learning_status_rank(status: str) -> int:
    return {
        "rolled_back": 7,
        "adopted": 6,
        "canary": 5,
        "candidate": 4,
        "rejected": 3,
        "completed": 2,
        "started": 1,
    }.get(status, 0)


def _merge_learning_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        learning_id = str(entry.get("id") or "")
        if not learning_id:
            continue
        existing = merged.get(learning_id)
        if existing is None:
            current = deepcopy(entry)
            history = current.get("history") if isinstance(current.get("history"), list) else []
            current["history"] = list(history)
            current["odinReviews"] = [current["odinReview"]] if current.get("odinReview") else []
            merged[learning_id] = current
            continue

        if _learning_status_rank(str(entry.get("status") or "")) > _learning_status_rank(
            str(existing.get("status") or "")
        ):
            existing["status"] = entry.get("status") or existing.get("status")

        if str(entry.get("observedAt") or "") > str(existing.get("observedAt") or ""):
            existing["observedAt"] = entry.get("observedAt")
            existing["eventType"] = entry.get("eventType") or existing.get("eventType")

        fill_keys = (
            "title",
            "artifactType",
            "riskClass",
            "policyDecision",
            "scope",
            "sourceEnvironmentId",
            "sourceValkyrieId",
            "targetFlockId",
            "evaluation",
            "negativeTransferRisk",
            "redaction",
            "promotedTool",
            "summary",
            "artifactContent",
            "artifactPath",
            "dreamRationale",
            "supersedes",
        )
        for key in fill_keys:
            if not existing.get(key) and entry.get(key):
                existing[key] = entry[key]

        if not existing.get("confidence") and entry.get("confidence"):
            existing["confidence"] = entry["confidence"]
        if not existing.get("sourceSignalIds") and entry.get("sourceSignalIds"):
            existing["sourceSignalIds"] = entry["sourceSignalIds"]
        if not existing.get("sourceEvidence") and entry.get("sourceEvidence"):
            existing["sourceEvidence"] = entry["sourceEvidence"]
        if not existing.get("feedback") and entry.get("feedback"):
            existing["feedback"] = entry["feedback"]
        existing["repetition"] = max(
            _as_int(existing.get("repetition"), 1),
            _as_int(entry.get("repetition"), 1),
        )

        review = entry.get("odinReview")
        if isinstance(review, dict) and review.get("reviewer"):
            existing["odinReview"] = review
            reviews = existing.setdefault("odinReviews", [])
            if review not in reviews:
                reviews.append(review)

        history = entry.get("history") if isinstance(entry.get("history"), list) else []
        existing.setdefault("history", []).extend(history)

    for entry in merged.values():
        entry["history"] = sorted(
            entry.get("history") or [],
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )
        resident_decisions = []
        for history in entry["history"]:
            if history.get("eventType") != registry.LEARNING_ADOPTION_RECORDED:
                continue
            resident_decisions.append(
                {
                    "residentValkyrieId": history.get("residentValkyrieId", ""),
                    "residentValkyrieName": history.get("residentValkyrieName", ""),
                    "action": history.get("action") or history.get("status") or "",
                    "relevant": history.get("relevant"),
                    "installedSkillName": history.get("installedSkillName", ""),
                    "observedAt": history.get("observedAt", ""),
                    "summary": history.get("summary", ""),
                }
            )
        entry["residentDecisions"] = resident_decisions
        entry["adoptedResidents"] = [
            decision
            for decision in resident_decisions
            if str(decision.get("action") or "").lower() in {"adopted", "adopt"}
        ]
        entry["rejectedResidents"] = [
            decision
            for decision in resident_decisions
            if str(decision.get("action") or "").lower() in {"rejected", "reject"}
        ]

    return list(merged.values())


def _tool_need_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    capability: str,
    status: str,
) -> dict[str, Any]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    details = fields or outcome or payload
    return {
        "id": str(event.get("event_id") or f"{capability}:{_event_timestamp(event)}"),
        "eventType": str(event.get("event_type") or ""),
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "taskId": str(payload.get("task_id") or ""),
        "capability": capability,
        "status": status,
        "summary": str(details.get("summary") or event.get("summary") or capability),
        "observedAt": _event_timestamp(event),
    }


def _capability_gap_from_details(details: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return the requested capability when a judgment/action exposes a gap.

    Resident Valkyries should not need a hard-coded catalog of every action they
    might one day perform. A judgment can surface the next missing capability as
    `action_capability` or, for older persona outputs, as a structured-looking
    `recommended_action`. The dashboard treats that as evolution pressure.
    """
    capability = str(
        details.get("action_capability") or payload.get("action_capability") or ""
    ).strip()
    if capability and capability.lower() not in {"none", "n/a", "no_action", "observe"}:
        return capability

    recommended = str(
        details.get("recommended_action") or payload.get("recommended_action") or ""
    ).strip()
    if not recommended or recommended.lower() in {"none", "n/a", "observe", "watch"}:
        return ""
    if "." in recommended or "_" in recommended:
        return recommended
    return ""


def _learning_status_for_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == registry.LEARNING_ADOPTION_RECORDED:
        action = str(payload.get("action") or "").lower()
        if action in {"adopted", "adopt"}:
            return "adopted"
        if action in {"canary", "canary_started"}:
            return "canary"
        if action in {"rejected", "reject"}:
            return "rejected"
        if action in {"overridden", "override"}:
            return "adopted"
        if action in {"regressed", "rollback", "rolled_back"}:
            return "rolled_back"
    if event_type == registry.LEARNING_PROMOTED:
        return str(payload.get("status") or "")
    if event_type == "valkyrie.evolution.built":
        return "candidate"
    if event_type == "valkyrie.evolution.activated":
        if payload.get("learning_id"):
            return "adopted"
        return "canary"
    if event_type == "valkyrie.evolution.proven":
        return "adopted"
    if event_type == "valkyrie.evolution.held":
        return "rejected"
    if event_type == "odin.court.decided":
        decision = str(payload.get("decision") or "").lower()
        if decision == "learning_adoption_blocked":
            return "rejected"
        if decision == "learning_adoption_allowed":
            return "canary"
        outcome = str(payload.get("outcome") or "").lower()
        if outcome == "rejected":
            return "rejected"
        if outcome == "approved":
            return "canary"
    if "rolled_back" in event_type:
        return "rolled_back"
    return ""


def _next_learning_scope(scope: str) -> str:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        return "environment"
    return LEARNING_SCOPES[min(index + 1, len(LEARNING_SCOPES) - 1)]


def _raw_learning_id(learning_id: str) -> str:
    return learning_id.removeprefix("live-")


def _previous_learning_scope(scope: str) -> str:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        return "private"
    return LEARNING_SCOPES[max(index - 1, 0)]


def _available_learning_scopes(scope: str) -> list[str]:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        index = 0
    return list(LEARNING_SCOPES[: index + 2])


def _learning_active_for_status(status: str) -> bool:
    return status in {"adopted", "canary"}


def _decision_summary(action: str, request: LearningDecisionRequest | None) -> str:
    reason = request.reason if request and request.reason else "no reason supplied"
    return f"{action.replace('_', ' ')} by {request.operatorId if request else 'system'}: {reason}"


def _decision_request_for_learning(
    learning_id: str,
    request: LearningFeedbackRequest | LearningReviseRequest,
) -> LearningDecisionRequest:
    """Adapt feedback/revise bodies to the shared learning-decision envelope."""
    return LearningDecisionRequest(
        learningId=learning_id,
        reason=request.reason,
        operatorId=request.operatorId,
        targetScope=getattr(request, "targetScope", ""),
    )


def _learning_feedback_action(
    verdict: str,
    status: str,
    *,
    current_scope: str = "",
    target_scope: str = "",
) -> str:
    """Map a feedback verdict onto the learning lifecycle action it triggers.

    ``feedback`` is the no-lifecycle action: the verdict is recorded and
    broadcast, and reinforcement happens resident-side. ``wrong_tier``
    defaults to promote so an unsupported target scope surfaces the
    promote path's 422.
    """
    if verdict == "dismissed":
        return "reject"
    if verdict == "bad_action":
        return "rollback" if status in {"adopted", "canary"} else "reject"
    if verdict != "wrong_tier":
        return "feedback"
    if target_scope not in LEARNING_SCOPES or current_scope not in LEARNING_SCOPES:
        return "promote"
    if LEARNING_SCOPES.index(target_scope) < LEARNING_SCOPES.index(current_scope):
        return "demote"
    return "promote"


def _learning_edits(request: LearningReviseRequest) -> dict[str, str]:
    """Map the non-empty revise fields onto learning record keys."""
    edits: dict[str, str] = {}
    if request.title.strip():
        edits["title"] = request.title
    if request.summary.strip():
        edits["summary"] = request.summary
    if request.content.strip():
        edits["artifactContent"] = request.content
    return edits


def _capability_from_signal_payload(event_type: str, payload: dict[str, Any]) -> str:
    namespace = event_type.removeprefix("signal.").removesuffix(".event")
    if not namespace:
        namespace = str(payload.get("namespace") or payload.get("source") or "generic")
    reason = str(payload.get("reason") or payload.get("kind") or "unknown")
    kind = str(payload.get("kind") or namespace)
    return f"inspect.{_slug(namespace)}.{_slug(kind)}.{_slug(reason)}"


def _learning_capability(learning: dict[str, Any]) -> str:
    artifact_content = str(learning.get("artifactContent") or "")
    match = re.search(r"^metadata:\n(?:.*\n)*?\s*capability:\s*([^\n]+)", artifact_content, re.M)
    if match:
        return match.group(1).strip().strip("`")
    for value in (
        str(learning.get("promotedTool") or ""),
        str(learning.get("title") or ""),
    ):
        if value.startswith("valkyrie-inspect-"):
            return "inspect." + value.removeprefix("valkyrie-inspect-").replace("-", ".")
        if value.startswith("inspect."):
            return value
    return ""


def _merge_learning_record(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return incoming
    merged = {**existing, **incoming}
    for key in (
        "artifactContent",
        "artifactPath",
        "artifactType",
        "promotedTool",
        "dreamRationale",
        "evaluation",
        "summary",
        "supersedes",
    ):
        if not incoming.get(key) and existing.get(key):
            merged[key] = existing[key]
    for key in ("odinReview", "sourceEvidence", "feedback"):
        if not incoming.get(key) and existing.get(key):
            merged[key] = existing[key]
    merged["repetition"] = max(
        _as_int(existing.get("repetition"), 1),
        _as_int(incoming.get("repetition"), 1),
    )
    merged["sourceSignalIds"] = list(
        dict.fromkeys(
            [
                *_as_string_list(existing.get("sourceSignalIds") or []),
                *_as_string_list(incoming.get("sourceSignalIds") or []),
            ]
        )
    )
    merged["history"] = [
        *(existing.get("history") if isinstance(existing.get("history"), list) else []),
        *(incoming.get("history") if isinstance(incoming.get("history"), list) else []),
    ][-30:]
    merged["active"] = _learning_active_for_status(str(merged.get("status") or ""))
    merged["currentScope"] = str(merged.get("scope") or merged.get("currentScope") or "private")
    merged["targetScope"] = _next_learning_scope(merged["currentScope"])
    merged["availableScopes"] = _available_learning_scopes(merged["currentScope"])
    return merged


def _dashboard_learning_from_telemetry(entry: dict[str, Any]) -> dict[str, Any]:
    scope = str(entry.get("scope") or "flock")
    status = str(entry.get("status") or "candidate")
    return {
        "id": str(entry.get("id") or ""),
        "title": str(entry.get("title") or entry.get("promotedTool") or "Generated capability"),
        "summary": str(entry.get("summary") or entry.get("evaluation") or ""),
        "scope": scope,
        "status": status,
        "sourceEnvironmentId": str(
            entry.get("sourceEnvironmentId") or entry.get("environmentId") or ""
        ),
        "sourceValkyrieId": str(entry.get("sourceValkyrieId") or entry.get("valkyrieId") or ""),
        "targetFlockId": str(entry.get("targetFlockId") or ""),
        "confidence": _as_float(entry.get("confidence"), 0.0),
        "evaluation": str(entry.get("evaluation") or entry.get("summary") or ""),
        "negativeTransferRisk": str(entry.get("negativeTransferRisk") or "low"),
        "redaction": str(entry.get("redaction") or "none"),
        "promotedTool": str(entry.get("promotedTool") or ""),
        "createdAt": str(entry.get("observedAt") or _now()),
        "active": status in {"adopted", "canary"},
        "currentScope": scope,
        "targetScope": _next_learning_scope(scope),
        "availableScopes": _available_learning_scopes(scope),
        "artifactContent": str(entry.get("artifactContent") or ""),
        "artifactPath": str(entry.get("artifactPath") or ""),
        "artifactType": str(entry.get("artifactType") or ""),
        "sourceSignalIds": _as_string_list(entry.get("sourceSignalIds") or []),
        "sourceEvidence": (
            entry.get("sourceEvidence") if isinstance(entry.get("sourceEvidence"), dict) else {}
        ),
        "dreamRationale": str(entry.get("dreamRationale") or ""),
        "odinReview": entry.get("odinReview") if isinstance(entry.get("odinReview"), dict) else {},
        "history": entry.get("history") if isinstance(entry.get("history"), list) else [],
        "canaryEnvironmentId": "",
        "operatorDecisions": [],
        "commandDelivery": entry.get("commandDelivery")
        if isinstance(entry.get("commandDelivery"), dict)
        else {},
        "feedback": entry.get("feedback") if isinstance(entry.get("feedback"), dict) else None,
        "repetition": _as_int(entry.get("repetition"), 1) or 1,
        "supersedes": str(entry.get("supersedes") or ""),
    }
