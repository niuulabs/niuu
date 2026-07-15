"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

from typing import Any

from ravn.api.valkyrie_event_projection import (
    _event_dict,
    _event_environment_id,
    _event_log_entry,
    _event_timestamp,
    _event_valkyrie_id,
    _event_valkyrie_name,
    _is_runtime_event,
    _payload_int,
    _structured_log_entry,
)
from ravn.api.valkyrie_learning_projection import (
    _capability_gap_from_details,
    _learning_entry,
    _learning_status_for_event,
    _merge_learning_entries,
    _tool_need_entry,
)
from ravn.api.valkyrie_projection_common import _empty_telemetry
from ravn.api.valkyrie_runtime_projection import _merge_runtime_entry, _runtime_entry


def _environment_telemetry_entry(entries: dict[str, dict[str, Any]], env_id: str) -> dict[str, Any]:
    entry = entries.get(env_id)
    if entry is None:
        entry = {
            "environmentId": env_id,
            "lastObservedAt": "",
            "pollsCompleted": 0,
            "pollFailures": 0,
            "signalsCollected": 0,
            "signalsPublished": 0,
            "duplicateSignals": 0,
            "tasksEnqueued": 0,
            "tasksStarted": 0,
            "tasksCompleted": 0,
            "tasksFailed": 0,
            "tasksDropped": 0,
            "judgments": 0,
            "actions": 0,
            "learningEvents": 0,
            "dreamCycles": 0,
        }
        entries[env_id] = entry
    return entry


def _aggregate_telemetry(
    events: list[dict[str, Any]],
    *,
    observed_at: str,
) -> dict[str, Any]:
    if not events:
        return _empty_telemetry(observed_at)

    totals = _empty_telemetry(observed_at)["totals"]
    by_environment: dict[str, dict[str, Any]] = {}
    recent_polls: list[dict[str, Any]] = []
    recent_tasks: list[dict[str, Any]] = []
    recent_outcomes: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    recent_logs: list[dict[str, Any]] = []
    recent_learning: list[dict[str, Any]] = []
    recent_tool_needs: list[dict[str, Any]] = []
    seen_tool_needs: set[tuple[str, str, str]] = set()
    runtime_by_key: dict[str, dict[str, Any]] = {}
    llm = {
        "status": "unknown",
        "model": "",
        "reflectionModel": "",
        "postSessionReflectionEnabled": False,
        "lastObservedAt": "",
    }

    for raw_event in events:
        event = _event_dict(raw_event)
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        timestamp = _event_timestamp(event)
        env_id = _event_environment_id(event, payload)
        entry = _environment_telemetry_entry(by_environment, env_id)
        entry["lastObservedAt"] = max(entry["lastObservedAt"], timestamp)
        totals["eventsObserved"] += 1
        # Routine cadence chatter has its own projections (recentPolls, the
        # runtime presence merge). Letting it into the activity feed evicts
        # the judgments and actions the timeline exists to show.
        if event_type not in {
            "valkyrie.signal_poll.completed",
            "valkyrie.presence.heartbeat",
        }:
            recent_events.append(_event_log_entry(event, payload))

        def append_tool_need(*, capability: str, status: str) -> None:
            task_id = str(payload.get("task_id") or event.get("correlation_id") or "")
            need_key = (env_id, task_id, capability)
            if need_key in seen_tool_needs:
                return
            seen_tool_needs.add(need_key)
            totals["toolRequests"] += 1
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=status,
                )
            )

        if event_type.startswith("ravn.log.") or event_type.startswith("valkyrie.log."):
            totals["logEvents"] += 1
            recent_logs.append(_structured_log_entry(event, payload))
        elif event_type.startswith("ravn.llm.") or event_type.startswith("llm."):
            totals["llmCalls"] += 1
            totals["llmTokens"] += _payload_int(payload, "total_tokens")
            if not totals["llmTokens"]:
                totals["llmTokens"] += _payload_int(payload, "tokens")
        elif event_type == "valkyrie.signal_poll.completed":
            collected = _payload_int(payload, "collected_count")
            published = _payload_int(payload, "published_count")
            duplicates = _payload_int(payload, "duplicate_count")
            enqueued = _payload_int(payload, "enqueued_task_count")
            # Polls are excluded from the activity feed, but they still prove
            # the resident is alive — merge identity into the presence map so
            # a poll-only resident never shows as configured/offline.
            poll_valkyrie_id = _event_valkyrie_id(payload)
            if poll_valkyrie_id:
                poll_runtime_key = f"{env_id}:{poll_valkyrie_id}"
                runtime_by_key[poll_runtime_key] = _merge_runtime_entry(
                    runtime_by_key.get(poll_runtime_key),
                    {
                        "environmentId": env_id,
                        "valkyrieId": poll_valkyrie_id,
                        "valkyrieName": _event_valkyrie_name(payload),
                        "observedAt": timestamp,
                    },
                )
            totals["pollsCompleted"] += 1
            totals["signalsCollected"] += collected
            totals["signalsPublished"] += published
            totals["duplicateSignals"] += duplicates
            totals["tasksEnqueued"] += enqueued
            entry["pollsCompleted"] += 1
            entry["signalsCollected"] += collected
            entry["signalsPublished"] += published
            entry["duplicateSignals"] += duplicates
            entry["tasksEnqueued"] += enqueued
            recent_polls.append(
                {
                    "environmentId": env_id,
                    "sourceId": payload.get("source_id", ""),
                    "status": "completed",
                    "collected": collected,
                    "published": published,
                    "duplicates": duplicates,
                    "tasksEnqueued": enqueued,
                    "durationMs": _payload_int(payload, "duration_ms"),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "valkyrie.signal_poll.failed":
            totals["pollFailures"] += 1
            entry["pollFailures"] += 1
            recent_polls.append(
                {
                    "environmentId": env_id,
                    "sourceId": payload.get("source_id", ""),
                    "status": "failed",
                    "error": payload.get("error", ""),
                    "observedAt": timestamp,
                }
            )
        elif _is_runtime_event(event):
            runtime_entry = _runtime_entry(event, payload, timestamp)
            runtime_key = (
                f"{runtime_entry['environmentId']}:{runtime_entry['valkyrieId'] or 'unknown'}"
            )
            runtime_by_key[runtime_key] = _merge_runtime_entry(
                runtime_by_key.get(runtime_key),
                runtime_entry,
            )
            if (
                event_type == "valkyrie.runtime.started"
                or payload.get("llm_model")
                or payload.get("reflection_model")
            ):
                llm = {
                    "status": "configured",
                    "model": str(payload.get("llm_model") or llm.get("model") or ""),
                    "reflectionModel": str(
                        payload.get("reflection_model") or llm.get("reflectionModel") or ""
                    ),
                    "postSessionReflectionEnabled": bool(
                        payload.get(
                            "post_session_reflection_enabled",
                            llm.get("postSessionReflectionEnabled"),
                        )
                    ),
                    "lastObservedAt": timestamp,
                }
            if event_type == "valkyrie.state.changed":
                totals["wakefulnessChanges"] += 1
                recent_learning.append(_learning_entry(event, payload, status="wakefulness"))
            elif event_type.startswith("valkyrie.dream."):
                totals["learningEvents"] += 1
                entry["learningEvents"] += 1
                recent_learning.append(
                    _learning_entry(event, payload, status=event_type.rsplit(".", 1)[-1])
                )
                if event_type == "valkyrie.dream.started":
                    totals["dreamCyclesStarted"] += 1
                    entry["dreamCycles"] += 1
                elif event_type == "valkyrie.dream.completed":
                    totals["dreamCyclesCompleted"] += 1
                elif event_type == "valkyrie.dream.noop":
                    totals["dreamCyclesNoop"] += 1
                    totals["dreamCyclesCompleted"] += 1
                elif event_type == "valkyrie.dream.failed":
                    totals["dreamCyclesFailed"] += 1
        elif event_type.startswith("signal."):
            totals["rawSignalEvents"] += 1
        elif event_type == "ravn.task.started":
            totals["tasksStarted"] += 1
            entry["tasksStarted"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": "started",
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "ravn.task.completed":
            outcome = str(payload.get("outcome") or "")
            totals["tasksCompleted"] += 1
            entry["tasksCompleted"] += 1
            if outcome not in {"success", "completed", "complete"}:
                totals["tasksFailed"] += 1
                entry["tasksFailed"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": (
                        "completed" if outcome in {"success", "completed", "complete"} else "failed"
                    ),
                    "outcome": outcome,
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "ravn.task.dropped":
            totals["tasksDropped"] += 1
            entry["tasksDropped"] += 1
            reason = str(payload.get("reason") or "")
            if "budget" in reason.lower() or "cap" in reason.lower():
                totals["budgetDrops"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": "dropped",
                    "reason": reason,
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("valkyrie.judgment."):
            totals["judgments"] += 1
            entry["judgments"] += 1
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
            details = fields or outcome or payload
            capability = _capability_gap_from_details(details, payload)
            if capability:
                append_tool_need(
                    capability=capability,
                    status=str(details.get("decision") or details.get("verdict") or "needed"),
                )
            recent_outcomes.append(
                {
                    "environmentId": env_id,
                    "type": "judgment",
                    "eventType": event_type,
                    "taskId": payload.get("task_id", ""),
                    "valkyrieId": payload.get("valkyrie_id", ""),
                    "verdict": details.get("verdict", payload.get("verdict", "")),
                    "tier": details.get("tier", payload.get("tier", "")),
                    "confidence": details.get("confidence", payload.get("confidence", 0)),
                    "recommendedAction": details.get(
                        "recommended_action",
                        payload.get("recommended_action", ""),
                    ),
                    "summary": details.get(
                        "summary",
                        payload.get("summary", event.get("summary", "")),
                    ),
                    "valid": payload.get("valid", True),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("valkyrie.action."):
            totals["actions"] += 1
            entry["actions"] += 1
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
            details = fields or outcome or payload
            capability = _capability_gap_from_details(details, payload)
            if capability:
                append_tool_need(capability=capability, status=event_type.rsplit(".", 1)[-1])
            recent_outcomes.append(
                {
                    "environmentId": env_id,
                    "type": "action",
                    "eventType": event_type,
                    "taskId": payload.get("task_id", ""),
                    "valkyrieId": payload.get("valkyrie_id", ""),
                    "verdict": details.get("verdict", payload.get("verdict", "")),
                    "tier": details.get("tier", payload.get("tier", "")),
                    "confidence": details.get("confidence", payload.get("confidence", 0)),
                    "recommendedAction": details.get(
                        "recommended_action",
                        details.get("action_capability", payload.get("recommended_action", "")),
                    ),
                    "summary": details.get(
                        "summary",
                        payload.get("summary", event.get("summary", "")),
                    ),
                    "valid": payload.get("valid", True),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("learning.") or event_type.startswith("flock.learning."):
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(
                _learning_entry(
                    event,
                    payload,
                    status=_learning_status_for_event(event_type, payload),
                )
            )
            if event_type == "learning.dream.started":
                totals["dreamCyclesStarted"] += 1
                entry["dreamCycles"] += 1
            elif event_type == "learning.dream.completed":
                totals["dreamCyclesCompleted"] += 1
            elif event_type == "learning.dream.noop":
                totals["dreamCyclesNoop"] += 1
                totals["dreamCyclesCompleted"] += 1
            elif event_type == "learning.dream.failed":
                totals["dreamCyclesFailed"] += 1
        elif event_type == "valkyrie.capability_gap.detected":
            capability = str(payload.get("capability_name") or event.get("summary") or "unknown")
            append_tool_need(capability=capability, status="needed")
            recent_learning.append(_learning_entry(event, payload, status="candidate"))
        elif event_type.startswith("valkyrie.evolution."):
            status = _learning_status_for_event(event_type, payload)
            totals["skillProposals"] += 1
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(_learning_entry(event, payload, status=status))
            capability = str(
                payload.get("capability_name")
                or payload.get("skill_name")
                or payload.get("artifact_name")
                or event.get("summary")
                or event_type
            )
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=status or event_type.rsplit(".", 1)[-1],
                )
            )
        elif event_type == "odin.court.decided":
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(
                _learning_entry(
                    event,
                    payload,
                    status=_learning_status_for_event(event_type, payload),
                )
            )
        elif (
            event_type.startswith("self_improvement.")
            or event_type.startswith("skill.")
            or event_type.startswith("tool.")
            or event_type == "skill_manage"
        ):
            totals["skillProposals"] += 1
            recent_learning.append(_learning_entry(event, payload))
            capability = str(
                payload.get("capability")
                or payload.get("tool")
                or payload.get("artifact_type")
                or event_type
            )
            totals["toolRequests"] += 1
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=event_type.rsplit(".", 1)[-1],
                )
            )
        elif event_type.startswith("flock."):
            totals["flockMessages"] += 1

    gaps: list[str] = []
    if totals["judgments"] == 0:
        if totals["tasksCompleted"] > 0:
            gaps.append("Tasks completed but no verified valkyrie.judgment.* events observed.")
        else:
            gaps.append("No verified valkyrie.judgment.* events observed.")
    if totals["actions"] == 0:
        gaps.append("No verified valkyrie.action.* events observed.")
    if totals["learningEvents"] == 0:
        gaps.append("No verified learning or flock.learning events observed.")
    runtime = sorted(
        runtime_by_key.values(),
        key=lambda item: item.get("observedAt", ""),
        reverse=True,
    )
    if totals["dreamCyclesStarted"] == 0 and totals["dreamCyclesNoop"] == 0:
        gaps.append("No verified dream-cycle events observed.")
    elif (
        totals["dreamCyclesStarted"] > 0
        and totals["learningEvents"] <= totals["dreamCyclesStarted"]
    ):
        gaps.append("Dream cycles are running, but no improvement artifacts were extracted yet.")
    if totals["toolRequests"] == 0:
        gaps.append("No verified tool/action capability requests observed.")
    elif totals["skillProposals"] == 0:
        gaps.append(
            "Capability gaps are visible, but no skill or self-improvement proposals "
            "have been observed yet."
        )
    if totals["skillProposals"] == 0 and totals["learningEvents"] == 0:
        gaps.append("No verified skill or self-improvement proposals observed.")
    if not runtime:
        gaps.append("No valkyrie.runtime.started events observed.")

    return {
        "source": "sleipnir_events",
        "verified": True,
        "lastObservedAt": max(_event_timestamp(_event_dict(event)) for event in events),
        "totals": totals,
        "byEnvironment": sorted(
            by_environment.values(),
            key=lambda item: (item["environmentId"] == "unknown", item["environmentId"]),
        ),
        "recentPolls": sorted(
            recent_polls,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentTasks": sorted(
            recent_tasks,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentOutcomes": sorted(
            recent_outcomes,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentEvents": sorted(
            recent_events,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:120],
        "recentLogs": sorted(
            recent_logs,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:120],
        "recentLearning": sorted(
            _merge_learning_entries(recent_learning),
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:60],
        "recentToolNeeds": sorted(
            recent_tool_needs,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:60],
        "runtime": runtime,
        "llm": llm,
        "gaps": gaps,
    }
