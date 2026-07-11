"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

from typing import Any

from ravn.api.valkyrie_config import (
    ValkyrieDashboardConfig,
    configured_environment_records,
)
from ravn.api.valkyrie_projection_common import (
    _as_float,
    _as_int,
    _as_string_list,
    _empty_telemetry,
    _environment_id,
    _field,
    _live_report,
    _now,
    _rollup_health,
    _valkyrie_id,
)

Dashboard = dict[str, Any]

def _configured_environment_entries(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        environment_id = _environment_id(record)
        name = str(_field(record, "name", default=environment_id))
        entries.append(
            {
                "id": environment_id,
                "name": name,
                "kind": str(_field(record, "kind", default="generic")),
                "health": str(_field(record, "health", default="watch")),
                "flockId": str(_field(record, "flockId", "flock_id", default="")),
                "topologyNodeIds": _as_string_list(
                    _field(record, "topologyNodeIds", "topology_node_ids", default=[])
                ),
                "signalCount": _as_int(_field(record, "signalCount", "signal_count", default=0)),
                "unresolvedSignalCount": _as_int(
                    _field(record, "unresolvedSignalCount", "unresolved_signal_count", default=0)
                ),
                "wakefulCount": _as_int(_field(record, "wakefulCount", "wakeful_count", default=1)),
                "dreamingCount": _as_int(
                    _field(record, "dreamingCount", "dreaming_count", default=0)
                ),
                "lastSignalAt": str(_field(record, "lastSignalAt", "last_signal_at", default="")),
                "actionAuthorities": _as_string_list(
                    _field(record, "actionAuthorities", "action_authorities", default=[])
                ),
                "transport": _field(record, "transport", default={}),
            },
        )
    return entries


def _configured_valkyrie_entries(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        environment_id = _environment_id(record)
        valkyrie = _field(record, "valkyrie", default={})
        if not isinstance(valkyrie, dict):
            valkyrie = {}
        combined = {**record, **valkyrie}
        valkyrie_id = _valkyrie_id(combined, environment_id)
        entries.append(
            {
                "id": valkyrie_id,
                "name": str(_field(combined, "valkyrieName", "valkyrie_name", default=valkyrie_id)),
                "environmentId": environment_id,
                "flockId": str(_field(combined, "flockId", "flock_id", default="")),
                "persona": str(_field(combined, "persona", default="valkyrie")),
                "specialty": str(_field(combined, "specialty", default="resident operations")),
                "wakefulness": str(_field(combined, "wakefulness", default="watching")),
                "autonomyMode": str(
                    _field(combined, "autonomyMode", "autonomy_mode", default="guarded")
                ),
                "status": str(_field(combined, "status", default="online")),
                "confidence": _as_float(_field(combined, "confidence", default=0.0)),
                "inboxSubjects": _as_string_list(
                    _field(combined, "inboxSubjects", "inbox_subjects", default=[])
                ),
                "toolCount": _as_int(_field(combined, "toolCount", "tool_count", default=0)),
                "lastDreamAt": str(_field(combined, "lastDreamAt", "last_dream_at", default="")),
                "lastActionAt": str(_field(combined, "lastActionAt", "last_action_at", default="")),
                "charter": str(_field(combined, "charter", default="")),
                "identitySource": "configured",
            },
        )
    return entries


def _configured_flock_entries(
    records: list[dict[str, Any]],
    environments: list[dict[str, Any]],
    valkyries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flocks: dict[str, dict[str, Any]] = {}
    environment_by_id = {entry["id"]: entry for entry in environments}
    valkyrie_by_environment = {entry["environmentId"]: entry for entry in valkyries}
    for record in records:
        environment_id = _environment_id(record)
        flock_id = str(_field(record, "flockId", "flock_id", default=""))
        if not flock_id:
            continue
        flock = _field(record, "flock", default={})
        if not isinstance(flock, dict):
            flock = {}
        entry = flocks.setdefault(
            flock_id,
            {
                "id": flock_id,
                "name": str(_field(flock, "name", default=flock_id)),
                "domain": str(_field(flock, "domain", default="")),
                "natsSubject": str(_field(flock, "natsSubject", "nats_subject", default="")),
                "environmentIds": [],
                "valkyrieIds": [],
                "learningIds": _as_string_list(
                    _field(flock, "learningIds", "learning_ids", default=[])
                ),
                "health": "healthy",
                "lastExchangeAt": str(
                    _field(flock, "lastExchangeAt", "last_exchange_at", default="")
                ),
            },
        )
        if environment_id in environment_by_id:
            entry["environmentIds"].append(environment_id)
        valkyrie = valkyrie_by_environment.get(environment_id)
        if valkyrie is not None:
            entry["valkyrieIds"].append(valkyrie["id"])
    for entry in flocks.values():
        entry["environmentIds"] = sorted(set(entry["environmentIds"]))
        entry["valkyrieIds"] = sorted(set(entry["valkyrieIds"]))
        entry["health"] = _rollup_health(
            [
                environment_by_id[environment_id]["health"]
                for environment_id in entry["environmentIds"]
                if environment_id in environment_by_id
            ]
        )
    return sorted(flocks.values(), key=lambda entry: entry["id"])


def _configured_huddle_entries(
    environments: list[dict[str, Any]],
    valkyries: list[dict[str, Any]],
    updated_at: str,
) -> list[dict[str, Any]]:
    """One standing operator huddle per environment.

    Every environment has a resident room (Environment.room_id); mirroring it
    here as an always-open huddle lets operators join and message the resident
    through the existing huddle routes without a huddle-opening event having
    fired first. No targetFlockId: these are environment rooms, so joining
    them never requires flock scope.
    """
    resident_by_environment = {entry["environmentId"]: entry for entry in valkyries}
    huddles: list[dict[str, Any]] = []
    for environment in environments:
        resident = resident_by_environment.get(environment["id"])
        huddles.append(
            {
                "id": f"huddle-{environment['id']}",
                "environmentId": environment["id"],
                "targetFlockId": "",
                "title": f"{environment['name']} huddle",
                "status": "open",
                "participantIds": [resident["id"]] if resident else [],
                "joined": False,
                "joinedParticipantId": "",
                "joinedDisplayName": "",
                "joinedAction": "",
                "messages": [],
                "lastActivityAt": updated_at,
            }
        )
    return huddles


def _initial_dashboard(config: ValkyrieDashboardConfig | None = None) -> Dashboard:
    records = configured_environment_records(config or ValkyrieDashboardConfig())
    environments = _configured_environment_entries(records)
    valkyries = _configured_valkyrie_entries(records)
    flocks = _configured_flock_entries(records, environments, valkyries)
    updated_at = _now()

    return {
        "environments": environments,
        "valkyries": valkyries,
        "flocks": flocks,
        "signals": [],
        "operationalStates": [],
        "judgments": [],
        "courtDecisions": [],
        "actions": [],
        "huddles": _configured_huddle_entries(environments, valkyries, updated_at),
        "learnings": [],
        "liveReport": _live_report(updated_at, environments=environments),
        "telemetry": _empty_telemetry(updated_at),
        "updatedAt": updated_at,
    }


def _signal_events(dashboard: Dashboard) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for signal in dashboard["signals"]:
        events.append(
            {
                "type": "signal",
                "id": f"event-{signal['id']}",
                "environmentId": signal["environmentId"],
                "summary": signal["summary"],
                "severity": signal["severity"],
                "timestamp": signal["receivedAt"],
            }
        )
    for learning in dashboard["learnings"]:
        events.append(
            {
                "type": "learning",
                "id": f"event-{learning['id']}",
                "environmentId": learning["sourceEnvironmentId"],
                "flockId": learning.get("targetFlockId"),
                "summary": learning["title"],
                "severity": "notice",
                "timestamp": learning["createdAt"],
            }
        )
    return events
