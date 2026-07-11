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

from ravn.api.valkyrie_learning_projection import (  # noqa: F401
    _learning_entry,
    _learning_status_rank,
    _merge_learning_entries,
    _tool_need_entry,
    _capability_gap_from_details,
    _learning_status_for_event,
    _next_learning_scope,
    _raw_learning_id,
    _previous_learning_scope,
    _available_learning_scopes,
    _learning_active_for_status,
    _decision_summary,
    _decision_request_for_learning,
    _learning_feedback_action,
    _learning_edits,
    _capability_from_signal_payload,
    _learning_capability,
    _merge_learning_record,
    _dashboard_learning_from_telemetry,
)

from ravn.api.valkyrie_runtime_projection import (  # noqa: F401
    _runtime_entry,
    _merge_runtime_entry,
    _telemetry_activity,
    _runtime_event_key,
    _huddle_role_for_action,
    _validate_huddle_join_scope,
    _resolve_huddle_message_author,
    _merge_observed_runtime,
)

from ravn.api.valkyrie_telemetry_projection import (  # noqa: F401
    _environment_telemetry_entry,
    _aggregate_telemetry,
)

from ravn.api.valkyrie_inventory_projection import (  # noqa: F401
    _configured_environment_entries,
    _configured_valkyrie_entries,
    _configured_flock_entries,
    _configured_huddle_entries,
    _initial_dashboard,
    _signal_events,
)


class ValkyrieDashboardProjection:
    def __init__(self, config: ValkyrieDashboardConfig | None = None) -> None:
        self._dashboard = _initial_dashboard(config)
        self._poll_count = 0
        self._raw_signal_events: list[dict[str, Any]] = []
        self._control_events: list[dict[str, Any]] = []
        self._runtime_events: dict[str, dict[str, Any]] = {}
        self._learning_decisions: dict[str, dict[str, Any]] = {}
        #: Operator edits applied in place; re-applied after live re-ingest.
        self._learning_revisions: dict[str, dict[str, str]] = {}
        #: Superseding candidates authored by revise; survive live re-ingest.
        self._authored_learnings: dict[str, dict[str, Any]] = {}
        self._seen_event_ids: set[str] = set()

    def dashboard(self) -> Dashboard:
        self._refresh_live_report()
        return _merge_observed_runtime(deepcopy(self._dashboard))

    def record_event(
        self,
        event: SleipnirEvent | dict[str, Any],
        *,
        refresh: bool = False,
    ) -> None:
        event_data = _event_dict(event)
        event_id = str(event_data.get("event_id") or "")
        if event_id:
            if event_id in self._seen_event_ids:
                return
            self._seen_event_ids.add(event_id)
            if len(self._seen_event_ids) > CONTROL_TELEMETRY_LIMIT + RAW_SIGNAL_TELEMETRY_LIMIT:
                self._seen_event_ids = set(
                    list(self._seen_event_ids)[
                        -(CONTROL_TELEMETRY_LIMIT + RAW_SIGNAL_TELEMETRY_LIMIT) :
                    ]
                )
        if _is_runtime_event(event_data):
            runtime_key = _runtime_event_key(event_data)
            existing = self._runtime_events.get(runtime_key)
            if existing is None:
                self._runtime_events[runtime_key] = event_data
            else:
                existing_payload = (
                    existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
                )
                incoming_payload = (
                    event_data.get("payload") if isinstance(event_data.get("payload"), dict) else {}
                )
                merged_payload = {
                    **existing_payload,
                    **{
                        key: value
                        for key, value in incoming_payload.items()
                        if value not in ("", 0, False, None, [])
                    },
                }
                merged_event = {**existing, **event_data, "payload": merged_payload}
                if str(existing.get("timestamp") or "") > str(event_data.get("timestamp") or ""):
                    merged_event["timestamp"] = existing.get("timestamp")
                self._runtime_events[runtime_key] = merged_event
        if _is_raw_signal_event(event_data):
            self._raw_signal_events.append(event_data)
            self._raw_signal_events = self._raw_signal_events[-RAW_SIGNAL_TELEMETRY_LIMIT:]
        else:
            self._control_events.append(event_data)
            self._control_events = self._control_events[-CONTROL_TELEMETRY_LIMIT:]
        if refresh:
            self._touch()
        else:
            self._dashboard["updatedAt"] = _now()

    def environments(self) -> list[dict[str, Any]]:
        return self.dashboard()["environments"]

    def environment(self, environment_id: str) -> dict[str, Any]:
        environment = next(
            (entry for entry in self._dashboard["environments"] if entry["id"] == environment_id),
            None,
        )
        if environment is None:
            raise HTTPException(status_code=404, detail="Environment not found")
        return deepcopy(environment)

    def flocks(self) -> list[dict[str, Any]]:
        return self.dashboard()["flocks"]

    def flock(self, flock_id: str) -> dict[str, Any]:
        flock = next(
            (entry for entry in self._dashboard["flocks"] if entry["id"] == flock_id),
            None,
        )
        if flock is None:
            raise HTTPException(status_code=404, detail="Flock not found")
        return deepcopy(flock)

    def huddle_for_room(self, huddle_id: str) -> dict[str, Any]:
        huddle = deepcopy(self._require_huddle(huddle_id))
        environment = next(
            (
                entry
                for entry in self._dashboard["environments"]
                if entry["id"] == huddle.get("environmentId")
            ),
            {},
        )
        if isinstance(environment, dict):
            huddle.setdefault(
                "environmentActionAuthorities",
                environment.get("actionAuthorities", []),
            )
        return huddle

    def join_huddle(self, request: HuddleJoinRequest) -> dict[str, Any]:
        huddle_id = request.huddleId
        huddle = self._require_huddle(huddle_id)
        _validate_huddle_join_scope(huddle, request)
        huddle["joined"] = True
        participant_id = request.participantId.strip()
        huddle["joinedParticipantId"] = participant_id
        huddle["joinedDisplayName"] = request.displayName.strip() or participant_id
        huddle["joinedAction"] = request.action
        if request.targetFlockId:
            huddle["targetFlockId"] = request.targetFlockId
        if participant_id not in huddle["participantIds"]:
            huddle["participantIds"].append(participant_id)
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def leave_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = self._require_huddle(huddle_id)
        participant_id = str(huddle.get("joinedParticipantId") or "").strip()
        huddle["joined"] = False
        huddle["participantIds"] = [
            entry for entry in huddle["participantIds"] if entry != participant_id
        ]
        huddle["joinedParticipantId"] = ""
        huddle["joinedDisplayName"] = ""
        huddle["joinedAction"] = ""
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def send_huddle_message(self, request: HuddleSendRequest) -> dict[str, Any]:
        huddle = self._require_huddle(request.huddleId)
        participant_id, display_name = _resolve_huddle_message_author(huddle, request)
        message = {
            "id": f"message-{len(huddle['messages']) + 1}",
            "huddleId": request.huddleId,
            "authorId": participant_id,
            "authorName": display_name,
            "body": request.body,
            "createdAt": _now(),
        }
        huddle["messages"].append(message)
        huddle["joined"] = True
        author_id = str(message["authorId"] or "").strip()
        if author_id and author_id not in huddle["participantIds"]:
            huddle["participantIds"].append(author_id)
        huddle["lastActivityAt"] = message["createdAt"]
        self._touch()
        return deepcopy(message)

    def learning(self, learning_id: str) -> dict[str, Any]:
        self._refresh_live_report()
        return deepcopy(self._require_learning(learning_id))

    def decide_learning(
        self,
        learning_id: str,
        status: str,
        request: LearningDecisionRequest | None = None,
        *,
        action: str = "",
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = status
        learning["active"] = _learning_active_for_status(status)
        if action == "override":
            learning["override"] = True
        self._append_learning_history(
            learning,
            event_type=f"valkyrie.learning.{action or status}",
            status=status,
            summary=_decision_summary(action or status, request),
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, action or status, request)
        self._touch()
        return deepcopy(learning)

    def promote_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        target_scope = request.targetScope or _next_learning_scope(
            str(learning.get("scope") or "private")
        )
        if target_scope not in LEARNING_SCOPES:
            raise HTTPException(status_code=422, detail="Unsupported learning scope")
        current_index = LEARNING_SCOPES.index(str(learning.get("scope") or "private"))
        target_index = LEARNING_SCOPES.index(target_scope)
        if target_index > current_index + 1:
            raise HTTPException(status_code=422, detail="Learning scope can only advance one step")
        learning["scope"] = target_scope
        learning["currentScope"] = target_scope
        learning["targetScope"] = _next_learning_scope(target_scope)
        learning["availableScopes"] = _available_learning_scopes(target_scope)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.promoted",
            status=str(learning.get("status") or "candidate"),
            summary=f"Promoted learning to {target_scope}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "promote", request)
        self._touch()
        return deepcopy(learning)

    def demote_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        target_scope = request.targetScope or _previous_learning_scope(
            str(learning.get("scope") or "private")
        )
        if target_scope not in LEARNING_SCOPES:
            raise HTTPException(status_code=422, detail="Unsupported learning scope")
        current_index = LEARNING_SCOPES.index(str(learning.get("scope") or "private"))
        target_index = LEARNING_SCOPES.index(target_scope)
        if target_index < current_index - 1:
            raise HTTPException(status_code=422, detail="Learning scope can only retreat one step")
        if target_index > current_index:
            raise HTTPException(status_code=422, detail="Use promote to advance learning scope")
        learning["scope"] = target_scope
        learning["currentScope"] = target_scope
        learning["targetScope"] = _next_learning_scope(target_scope)
        learning["availableScopes"] = _available_learning_scopes(target_scope)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.demoted",
            status=str(learning.get("status") or "candidate"),
            summary=f"Demoted learning to {target_scope}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "demote", request)
        self._touch()
        return deepcopy(learning)

    def canary_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = "canary"
        learning["active"] = True
        learning["canaryEnvironmentId"] = request.canaryEnvironmentId or str(
            learning.get("sourceEnvironmentId") or ""
        )
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.canary_started",
            status="canary",
            summary=f"Started canary in {learning['canaryEnvironmentId'] or 'source environment'}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "canary", request)
        self._touch()
        return deepcopy(learning)

    def rollback_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = "rolled_back"
        learning["active"] = False
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.rolled_back",
            status="rolled_back",
            summary=_decision_summary("rollback", request),
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "rollback", request)
        self._touch()
        return deepcopy(learning)

    def record_learning_feedback(
        self,
        learning_id: str,
        request: LearningFeedbackRequest,
    ) -> dict[str, Any]:
        """Attach one operator feedback verdict to a learning record."""
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["feedback"] = {
            "verdict": request.verdict,
            "reason": request.reason,
            "operatorId": request.operatorId,
            "recordedAt": _now(),
        }
        decision_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.feedback",
            status=str(learning.get("status") or ""),
            summary=f"Operator feedback: {request.verdict}",
            request=decision_request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "feedback", decision_request)
        self._touch()
        return deepcopy(learning)

    def revise_learning_in_place(
        self,
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        """Apply operator edits to a not-yet-active learning in place."""
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        edits = _learning_edits(request)
        learning.update(edits)
        self._learning_revisions[learning_id] = {
            **self._learning_revisions.get(learning_id, {}),
            **edits,
        }
        decision_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.revised",
            status=str(learning.get("status") or ""),
            summary=f"Revised in place: {', '.join(sorted(edits))}",
            request=decision_request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "revised", decision_request)
        self._touch()
        return deepcopy(learning)

    def revise_learning_supersede(
        self,
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        """Author a superseding candidate for an active learning.

        Active (adopted/canary) learnings are never mutated in place — the
        revision becomes a new candidate that re-enters the review flow.
        """
        self._refresh_live_report()
        old = self._require_learning(learning_id)
        revision_number = 1 + sum(
            1
            for entry in self._dashboard.get("learnings", [])
            if isinstance(entry, dict)
            and str(entry.get("id") or "").startswith(f"{learning_id}:rev")
        )
        new_id = f"{learning_id}:rev{revision_number}"
        candidate = deepcopy(old)
        candidate.update(_learning_edits(request))
        candidate.update(
            {
                "id": new_id,
                "status": "candidate",
                "active": False,
                "supersedes": learning_id,
                "feedback": None,
                "canaryEnvironmentId": "",
                "commandDelivery": {},
                "createdAt": _now(),
            }
        )
        decision_request = _decision_request_for_learning(new_id, request)
        self._append_learning_history(
            candidate,
            event_type="valkyrie.learning.revised",
            status="candidate",
            summary=f"Supersedes {learning_id}",
            request=decision_request,
        )
        self._dashboard["learnings"].insert(0, candidate)
        self._authored_learnings[new_id] = candidate
        old_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            old,
            event_type="valkyrie.learning.revised",
            status=str(old.get("status") or ""),
            summary=f"Superseded by {new_id}",
            request=old_request,
        )
        self._remember_learning_decision(old)
        self._record_learning_control_event(candidate, "revised", decision_request)
        self._touch()
        return deepcopy(candidate)

    def replay_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        self._refresh_live_report()
        event_type = str(signal.get("event_type") or signal.get("eventType") or "")
        payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
        payload = payload if isinstance(payload, dict) else {}
        capability = _capability_from_signal_payload(event_type, payload)
        learning = self._active_learning_for_capability(capability)
        signal_id = str(payload.get("signal_id") or payload.get("signalId") or "operator-signal")
        skill_name = (
            str(learning.get("promotedTool") or learning.get("title") or "") if learning else ""
        )
        decision = {
            "signalId": signal_id,
            "capabilityName": capability,
            "decision": (
                "inspect_with_adopted_learning" if learning else "defer_and_request_capability"
            ),
            "skillName": skill_name,
            "learningId": str(learning.get("id") or "") if learning else "",
            "confidence": 0.86 if learning else 0.42,
            "rationale": (
                "An active adopted/canary learning now matches this signal capability."
                if learning
                else "No active adopted learning matches this signal capability."
            ),
            "usedAdoptedLearning": learning is not None,
            "observedAt": _now(),
        }
        self.record_event(
            {
                "event_type": registry.VALKYRIE_JUDGMENT_PROPOSED,
                "source": "ravn:valkyrie-dashboard-replay",
                "summary": f"{decision['decision']} for {decision['signalId']}",
                "urgency": 0.4,
                "domain": "infrastructure",
                "timestamp": decision["observedAt"],
                "correlation_id": "valkyrie-dashboard-replay",
                "payload": {
                    "signal_id": decision["signalId"],
                    "capability_name": capability,
                    "decision": decision["decision"],
                    "confidence": decision["confidence"],
                    "skill_name": decision["skillName"],
                    "learning_id": decision["learningId"],
                    "rationale": decision["rationale"],
                },
            }
        )
        return decision

    def record_learning_command_delivery(
        self,
        learning_id: str,
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        learning = self._require_learning(learning_id)
        learning["commandDelivery"] = delivery
        self._remember_learning_decision(learning)
        self._touch()
        return deepcopy(learning)

    def _active_learning_for_capability(self, capability: str) -> dict[str, Any] | None:
        for learning in self._dashboard.get("learnings", []):
            if not isinstance(learning, dict) or not learning.get("active"):
                continue
            if str(learning.get("status") or "") not in {"adopted", "canary"}:
                continue
            if _learning_capability(learning) == capability:
                return learning
        return None

    def _append_learning_history(
        self,
        learning: dict[str, Any],
        *,
        event_type: str,
        status: str,
        summary: str,
        request: LearningDecisionRequest | None,
    ) -> None:
        history = learning.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "eventType": event_type,
                "status": status,
                "summary": summary,
                "operatorId": request.operatorId if request else "system",
                "reason": request.reason if request else "",
                "observedAt": _now(),
            }
        )
        learning["history"] = history[-30:]

    def _record_learning_control_event(
        self,
        learning: dict[str, Any],
        action: str,
        request: LearningDecisionRequest | None,
    ) -> None:
        self._control_events.append(
            {
                "event_type": f"valkyrie.learning.{action}",
                "source": "ravn:valkyrie-dashboard",
                "summary": _decision_summary(action, request),
                "urgency": 0.35,
                "domain": "infrastructure",
                "timestamp": _now(),
                "correlation_id": "valkyrie-learning-ui",
                "payload": {
                    "learning_id": learning.get("id", ""),
                    "title": learning.get("title", ""),
                    "status": learning.get("status", ""),
                    "scope": learning.get("scope", ""),
                    "active": learning.get("active", False),
                    "operator_id": request.operatorId if request else "system",
                    "reason": request.reason if request else "",
                    "target_scope": request.targetScope if request else "",
                    "canary_environment_id": request.canaryEnvironmentId if request else "",
                },
            }
        )
        self._control_events = self._control_events[-CONTROL_TELEMETRY_LIMIT:]

    def _remember_learning_decision(self, learning: dict[str, Any]) -> None:
        learning_id = str(learning.get("id") or "")
        if not learning_id:
            return
        history = learning.get("history") if isinstance(learning.get("history"), list) else []
        decision_history = [
            entry
            for entry in history
            if isinstance(entry, dict)
            and str(entry.get("eventType") or "").startswith("valkyrie.learning.")
        ]
        self._learning_decisions[learning_id] = {
            "status": str(learning.get("status") or ""),
            "active": bool(learning.get("active")),
            "scope": str(learning.get("scope") or learning.get("currentScope") or ""),
            "currentScope": str(learning.get("currentScope") or learning.get("scope") or ""),
            "targetScope": str(learning.get("targetScope") or ""),
            "availableScopes": (
                learning.get("availableScopes")
                if isinstance(learning.get("availableScopes"), list)
                else []
            ),
            "canaryEnvironmentId": str(learning.get("canaryEnvironmentId") or ""),
            "override": bool(learning.get("override")),
            "commandDelivery": (
                learning.get("commandDelivery")
                if isinstance(learning.get("commandDelivery"), dict)
                else {}
            ),
            "feedback": (
                learning.get("feedback") if isinstance(learning.get("feedback"), dict) else {}
            ),
            "supersedes": str(learning.get("supersedes") or ""),
            "decisionHistory": decision_history[-30:],
        }

    def update_autonomy(self, request: AutonomyUpdateRequest) -> Dashboard:
        valid_modes = {"guarded", "autonomous", "yolo"}
        if request.mode not in valid_modes:
            raise HTTPException(status_code=422, detail="Unsupported autonomy mode")
        valkyrie = next(
            (entry for entry in self._dashboard["valkyries"] if entry["id"] == request.valkyrieId),
            None,
        )
        if valkyrie is None:
            raise HTTPException(status_code=404, detail="Valkyrie not found")
        valkyrie["autonomyMode"] = request.mode
        self._touch()
        return self.dashboard()

    def events(self) -> list[dict[str, Any]]:
        return _signal_events(self._dashboard)

    def telemetry_events(
        self,
        *,
        limit: int = 200,
        event_type: str = "",
        environment_id: str = "",
        valkyrie_id: str = "",
        contains: str = "",
    ) -> list[dict[str, Any]]:
        self._refresh_live_report()
        telemetry_events = [*self._raw_signal_events, *self._control_events]
        retained_event_ids = {
            str(event.get("event_id") or "")
            for event in telemetry_events
            if isinstance(event, dict) and str(event.get("event_id") or "")
        }
        telemetry_events.extend(
            event
            for event in self._runtime_events.values()
            if str(event.get("event_id") or "") not in retained_event_ids
        )
        filtered = []
        for event in telemetry_events:
            normalized = _event_dict(event)
            payload = normalized.get("payload")
            filtered.append(
                _event_log_entry(
                    normalized,
                    payload if isinstance(payload, dict) else {},
                )
            )
        if event_type:
            filtered = [
                event for event in filtered if str(event.get("eventType") or "") == event_type
            ]
        if environment_id:
            canonical_environment_id = _canonical_environment_id(environment_id)
            filtered = [
                event
                for event in filtered
                if str(event.get("environmentId") or "") == canonical_environment_id
            ]
        if valkyrie_id:
            filtered = [
                event for event in filtered if str(event.get("valkyrieId") or "") == valkyrie_id
            ]
        if contains:
            needle = contains.lower()
            filtered = [event for event in filtered if needle in json.dumps(event).lower()]
        bounded_limit = max(1, min(int(limit or 200), 1_000))
        filtered = sorted(
            filtered,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )
        return deepcopy(filtered[:bounded_limit])

    def logs(self) -> list[dict[str, Any]]:
        telemetry = self.dashboard().get("telemetry", {})
        if not isinstance(telemetry, dict):
            return []
        logs = telemetry.get("recentLogs", [])
        return deepcopy(logs if isinstance(logs, list) else [])

    def _touch(self) -> None:
        self._dashboard["updatedAt"] = _now()
        self._refresh_live_report()

    def _refresh_live_report(self) -> None:
        self._poll_count += 1
        observed_at = _now()
        telemetry_events = [*self._raw_signal_events, *self._control_events]
        retained_event_ids = {
            str(event.get("event_id") or "")
            for event in telemetry_events
            if isinstance(event, dict) and str(event.get("event_id") or "")
        }
        telemetry_events.extend(
            event
            for event in self._runtime_events.values()
            if str(event.get("event_id") or "") not in retained_event_ids
        )
        self._dashboard["liveReport"] = _live_report(
            observed_at,
            self._poll_count,
            environments=self._dashboard["environments"],
        )
        self._dashboard["telemetry"] = _aggregate_telemetry(
            telemetry_events,
            observed_at=observed_at,
        )
        seeded_signals = [
            entry
            for entry in self._dashboard.get("signals", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["signals"] = sorted(
            [*_signals_from_events(telemetry_events), *seeded_signals],
            key=lambda item: item.get("receivedAt", ""),
            reverse=True,
        )[:60]
        seeded_decisions = [
            entry
            for entry in self._dashboard.get("courtDecisions", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["courtDecisions"] = sorted(
            [*_court_decisions_from_events(telemetry_events), *seeded_decisions],
            key=lambda item: item.get("createdAt", ""),
            reverse=True,
        )[:60]
        seeded_states = [
            entry
            for entry in self._dashboard.get("operationalStates", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["operationalStates"] = sorted(
            [*_operational_states_from_events(telemetry_events), *seeded_states],
            key=lambda item: item.get("updatedAt", ""),
            reverse=True,
        )[:60]
        self._sync_live_learnings()
        self._dashboard["updatedAt"] = observed_at

    def _sync_live_learnings(self) -> None:
        telemetry = self._dashboard.get("telemetry")
        if not isinstance(telemetry, dict):
            return
        recent = telemetry.get("recentLearning")
        if not isinstance(recent, list):
            return
        seeded = [
            entry
            for entry in self._dashboard.get("learnings", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        by_id = {str(entry.get("id") or ""): entry for entry in seeded}
        for item in sorted(recent, key=lambda entry: str(entry.get("observedAt") or "")):
            if not isinstance(item, dict):
                continue
            learning = _dashboard_learning_from_telemetry(item)
            if not learning["id"]:
                continue
            learning["id"] = f"live-{learning['id']}"
            existing = by_id.get(learning["id"])
            by_id[learning["id"]] = _merge_learning_record(existing, learning)
            revision = self._learning_revisions.get(learning["id"])
            if revision:
                by_id[learning["id"]].update(revision)
            decision = self._learning_decisions.get(learning["id"])
            if decision:
                for key in (
                    "status",
                    "active",
                    "scope",
                    "currentScope",
                    "targetScope",
                    "availableScopes",
                    "canaryEnvironmentId",
                    "override",
                    "commandDelivery",
                    "feedback",
                ):
                    if key in decision and decision[key] not in ("", [], {}):
                        by_id[learning["id"]][key] = decision[key]
                decision_history = (
                    decision.get("decisionHistory")
                    if isinstance(decision.get("decisionHistory"), list)
                    else []
                )
                if decision_history:
                    existing_history = (
                        by_id[learning["id"]].get("history")
                        if isinstance(by_id[learning["id"]].get("history"), list)
                        else []
                    )
                    telemetry_history = [
                        entry
                        for entry in existing_history
                        if not (
                            isinstance(entry, dict)
                            and str(entry.get("eventType") or "").startswith("valkyrie.learning.")
                        )
                    ]
                    by_id[learning["id"]]["history"] = [
                        *telemetry_history,
                        *decision_history,
                    ][-30:]
        for learning_id, authored in self._authored_learnings.items():
            # Superseding candidates are authored here, not observed in
            # telemetry — keep the live records across re-ingest.
            by_id[learning_id] = authored
        self._dashboard["learnings"] = sorted(
            by_id.values(),
            key=lambda entry: str(entry.get("createdAt") or ""),
            reverse=True,
        )

    def _require_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = next(
            (entry for entry in self._dashboard["huddles"] if entry["id"] == huddle_id),
            None,
        )
        if huddle is None:
            raise HTTPException(status_code=404, detail="Huddle not found")
        return huddle

    def _require_learning(self, learning_id: str) -> dict[str, Any]:
        learning = next(
            (entry for entry in self._dashboard["learnings"] if entry["id"] == learning_id),
            None,
        )
        if learning is None:
            raise HTTPException(status_code=404, detail="Learning not found")
        learning.setdefault("feedback", None)
        learning.setdefault("repetition", 1)
        learning.setdefault("supersedes", "")
        return learning
