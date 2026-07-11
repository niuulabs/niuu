"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ravn.api.valkyrie_event_projection import (
    _event_environment_id,
    _event_valkyrie_id,
    _event_valkyrie_name,
)
from ravn.api.valkyrie_projection_common import (
    _as_int,
    _as_string_list,
    _canonical_environment_id,
)
from ravn.api.valkyrie_requests import HuddleJoinRequest, HuddleSendRequest

Dashboard = dict[str, Any]

def _runtime_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    wakefulness = ""
    if event_type == "valkyrie.state.changed":
        wakefulness = str(payload.get("new_state") or "")
    elif event_type == "valkyrie.dream.started":
        wakefulness = "dreaming"
    return {
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "valkyrieName": _event_valkyrie_name(payload),
        "residentPersonality": str(payload.get("resident_personality") or ""),
        "charter": str(payload.get("charter") or ""),
        "signalTaskSeverities": _as_string_list(payload.get("signal_task_severities")),
        "sourceCount": payload.get("source_count", 0),
        "driveLoopEnabled": bool(payload.get("drive_loop_enabled")),
        "initiativeEnabled": bool(payload.get("initiative_enabled")),
        "pollIntervalSeconds": payload.get("poll_interval_seconds", 0),
        "wakefulness": wakefulness,
        "lastDreamAt": timestamp
        if event_type in {"valkyrie.dream.completed", "valkyrie.dream.noop"}
        else "",
        "observedAt": timestamp,
    }


def _merge_runtime_entry(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return dict(incoming)
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "observedAt":
            merged[key] = max(str(merged.get(key) or ""), str(value or ""))
        elif value not in ("", 0, False, None, []):
            merged[key] = value
    return merged


def _telemetry_activity(
    telemetry: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    activity_by_env: dict[str, str] = {}
    activity_by_valkyrie: dict[str, dict[str, str]] = {}

    for entry in telemetry.get("byEnvironment", []):
        if not isinstance(entry, dict):
            continue
        env_id = str(entry.get("environmentId") or "")
        observed_at = str(entry.get("lastObservedAt") or "")
        if env_id and observed_at:
            activity_by_env[env_id] = max(activity_by_env.get(env_id, ""), observed_at)

    for entry in telemetry.get("recentEvents", []):
        if not isinstance(entry, dict):
            continue
        observed_at = str(entry.get("observedAt") or "")
        if not observed_at:
            continue
        env_id = str(entry.get("environmentId") or "")
        if env_id:
            activity_by_env[env_id] = max(activity_by_env.get(env_id, ""), observed_at)
        valkyrie_id = str(entry.get("valkyrieId") or "")
        if not valkyrie_id:
            continue
        existing = activity_by_valkyrie.get(valkyrie_id)
        if existing is None or observed_at > existing.get("observedAt", ""):
            activity_by_valkyrie[valkyrie_id] = {
                "observedAt": observed_at,
                "valkyrieName": str(entry.get("valkyrieName") or ""),
            }

    return activity_by_env, activity_by_valkyrie


def _runtime_event_key(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    env_id = str(payload.get("environment_id") or payload.get("environmentId") or "unknown")
    valkyrie_id = str(payload.get("valkyrie_id") or payload.get("valkyrieId") or "unknown")
    return f"{env_id}:{valkyrie_id}"


def _huddle_role_for_action(action: str) -> str:
    normalized = action.strip().lower().replace("-", "_")
    roles = {
        "observe": "observer",
        "reply": "observer",
        "message": "observer",
        "teach": "teacher",
        "correct": "teacher",
        "approve": "approver",
        "authorize": "approver",
        "debug": "debugger",
        "own": "owner",
        "change_autonomy": "owner",
    }
    if normalized not in roles:
        raise HTTPException(status_code=422, detail=f"Unsupported huddle action: {action}")
    return roles[normalized]


def _validate_huddle_join_scope(huddle: dict[str, Any], request: HuddleJoinRequest) -> None:
    if request.huddleId != str(huddle.get("id") or ""):
        raise HTTPException(status_code=422, detail="Huddle id mismatch")
    if not request.participantId.strip():
        raise HTTPException(status_code=422, detail="participantId is required")
    huddle_flock = str(huddle.get("targetFlockId") or huddle.get("target_flock_id") or "").strip()
    requested_flock = request.targetFlockId.strip()
    if huddle_flock and not requested_flock:
        raise HTTPException(status_code=422, detail="targetFlockId is required for flock huddles")
    if huddle_flock and requested_flock and huddle_flock != requested_flock:
        raise HTTPException(
            status_code=409,
            detail=f"Huddle belongs to flock {huddle_flock}, not {requested_flock}",
        )
    _huddle_role_for_action(request.action)


def _resolve_huddle_message_author(
    huddle: dict[str, Any],
    request: HuddleSendRequest,
) -> tuple[str, str]:
    if request.huddleId != str(huddle.get("id") or ""):
        raise HTTPException(status_code=422, detail="Huddle id mismatch")
    if not request.body.strip():
        raise HTTPException(status_code=422, detail="Message body is required")
    participant_id = str(huddle.get("joinedParticipantId") or "").strip()
    if not huddle.get("joined") or not participant_id:
        raise HTTPException(status_code=422, detail="Join huddle before sending messages")
    requested_author_id = request.authorId.strip()
    if not requested_author_id:
        raise HTTPException(status_code=422, detail="authorId is required")
    if requested_author_id and requested_author_id != participant_id:
        raise HTTPException(
            status_code=409,
            detail=f"Huddle is joined as {participant_id}, not {requested_author_id}",
        )
    display_name = str(huddle.get("joinedDisplayName") or participant_id).strip() or participant_id
    return participant_id, display_name


def _merge_observed_runtime(dashboard: Dashboard) -> Dashboard:
    telemetry = dashboard.get("telemetry") if isinstance(dashboard.get("telemetry"), dict) else {}
    runtime = telemetry.get("runtime") if isinstance(telemetry.get("runtime"), list) else []
    activity_by_env, activity_by_valkyrie = _telemetry_activity(telemetry)
    observed_by_id = {
        str(entry.get("valkyrieId") or ""): entry
        for entry in runtime
        if isinstance(entry, dict) and str(entry.get("valkyrieId") or "")
    }
    observed_by_env: dict[str, dict[str, Any]] = {}
    for entry in runtime:
        if not isinstance(entry, dict):
            continue
        env_id = _canonical_environment_id(entry.get("environmentId"))
        if not env_id:
            continue
        observed_by_env[env_id] = entry
    for environment in dashboard.get("environments", []):
        if not isinstance(environment, dict):
            continue
        environment_id = str(environment.get("id") or "")
        observed = observed_by_env.get(environment_id)
        observed_at = str(observed.get("observedAt") or "") if observed else ""
        activity_at = activity_by_env.get(environment_id, "")
        last_seen_at = max(observed_at, activity_at)
        environment["identitySource"] = "observed" if last_seen_at else "configured"
        if last_seen_at:
            environment["lastSignalAt"] = max(
                str(environment.get("lastSignalAt") or ""),
                last_seen_at,
            )
            environment["wakefulCount"] = max(_as_int(environment.get("wakefulCount"), 0), 1)

    known_valkyrie_ids = set()
    for valkyrie in dashboard.get("valkyries", []):
        if not isinstance(valkyrie, dict):
            continue
        valkyrie_id = str(valkyrie.get("id") or "")
        known_valkyrie_ids.add(valkyrie_id)
        observed = observed_by_id.get(valkyrie_id)
        activity = activity_by_valkyrie.get(valkyrie_id)
        if observed is None and activity is None:
            valkyrie["identitySource"] = "configured"
            continue
        if observed and observed.get("valkyrieName"):
            valkyrie["name"] = str(observed["valkyrieName"])
        elif activity and activity.get("valkyrieName"):
            valkyrie["name"] = str(activity["valkyrieName"])
        if observed and observed.get("residentPersonality"):
            valkyrie["specialty"] = str(observed["residentPersonality"])
        if observed and observed.get("charter"):
            valkyrie["charter"] = str(observed["charter"])
        if observed and observed.get("signalTaskSeverities"):
            valkyrie["signalTaskSeverities"] = _as_string_list(observed["signalTaskSeverities"])
        valkyrie["identitySource"] = "observed"
        valkyrie["status"] = "online"
        observed_at = str(observed.get("observedAt") or "") if observed else ""
        activity_at = str(activity.get("observedAt") or "") if activity else ""
        valkyrie["lastObservedAt"] = max(
            str(valkyrie.get("lastObservedAt") or ""),
            observed_at,
            activity_at,
        )
        if observed and observed.get("wakefulness"):
            valkyrie["wakefulness"] = str(observed["wakefulness"])
        if observed and observed.get("lastDreamAt"):
            valkyrie["lastDreamAt"] = str(observed["lastDreamAt"])

    for valkyrie_id, observed in observed_by_id.items():
        if valkyrie_id in known_valkyrie_ids:
            continue
        dashboard.setdefault("valkyries", []).append(
            {
                "id": valkyrie_id,
                "name": str(observed.get("valkyrieName") or valkyrie_id),
                "environmentId": str(observed.get("environmentId") or "unknown"),
                "flockId": "",
                "persona": "observed-valkyrie",
                "specialty": str(observed.get("residentPersonality") or "observed resident"),
                "charter": str(observed.get("charter") or ""),
                "signalTaskSeverities": _as_string_list(observed.get("signalTaskSeverities")),
                "wakefulness": str(observed.get("wakefulness") or "watching"),
                "autonomyMode": "autonomous" if observed.get("driveLoopEnabled") else "guarded",
                "status": "online",
                "confidence": 0.0,
                "inboxSubjects": [],
                "toolCount": 0,
                "lastDreamAt": str(observed.get("lastDreamAt") or ""),
                "lastActionAt": "",
                "identitySource": "observed",
                "lastObservedAt": str(observed.get("observedAt") or ""),
            }
        )
    return dashboard
