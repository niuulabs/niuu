"""Dashboard-side mirror of learned skills installed by resident Valkyries.

A judgment only ever references a learned skill by NAME while the skill body
lives on the resident's disk. Two events carry the full artifact (markdown
content, tool code, tests, requirements): ``valkyrie.evolution.activated``
fires once at ADOPTION time, and ``valkyrie.evolution.skill_inventory`` is a
snapshot the resident republishes on startup and a heartbeat for every skill
it currently carries. Activation alone is not enough — it is rare and
historical, so a dashboard that starts with REPLAY_SECONDS=0 would never see a
skill actively referenced in judgments. The inventory snapshot fixes that: the
mirror keeps the latest record per (environment, skill) from either event and
serves it to the dashboard so an operator can open "handled with learned skill
X" and actually read X. Like :class:`ValkyrieDashboardProjection`, the mirror
is in-memory and rebuilds from the telemetry stream after a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ravn.domain.valkyrie_history import canonical_environment_id
from ravn.valkyrie_evolution.resident_learning import (
    EVOLUTION_ACTIVATED_EVENT,
    EVOLUTION_SKILL_INVENTORY_EVENT,
)
from sleipnir.domain.events import SleipnirEvent

#: Event types that carry a full learned-skill record the mirror ingests: the
#: adoption-time activation and the periodic live-inventory snapshot.
_SKILL_RECORD_EVENT_TYPES = frozenset({EVOLUTION_ACTIVATED_EVENT, EVOLUTION_SKILL_INVENTORY_EVENT})

#: Full-record keys withheld from list summaries — the list endpoint answers
#: "which skills does this environment have", not "show me the code".
_SUMMARY_EXCLUDED_KEYS = frozenset({"content", "toolCode", "testCode", "requirements", "manifest"})


def _event_dict(event: SleipnirEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, SleipnirEvent):
        return event.to_dict()
    return dict(event)


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _timestamp(event: dict[str, Any]) -> str:
    value = event.get("timestamp")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


def skill_record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a learned-skill record for an activation/inventory event; None otherwise.

    Both ``valkyrie.evolution.activated`` and ``valkyrie.evolution.skill_inventory``
    carry the same enriched record shape, so the extraction is identical — the
    mirror's latest-wins-per-(env, skill) then keeps whichever arrived most
    recently, which is the correct semantics for a live inventory.
    """
    if str(event.get("event_type") or "") not in _SKILL_RECORD_EVENT_TYPES:
        return None
    payload = _payload(event)
    event_type = str(event.get("event_type") or "")
    skill_name = str(payload.get("skill_name") or "").strip()
    if not skill_name:
        return None
    manifest = payload.get("learned_tool_manifest")
    observed_at = _timestamp(event)
    adopted_at = str(payload.get("adopted_at") or "")
    if not adopted_at and event_type == EVOLUTION_ACTIVATED_EVENT:
        adopted_at = observed_at
    valkyrie_id = str(payload.get("valkyrie_id") or "")
    source_valkyrie_id = str(payload.get("source_valkyrie_id") or "")
    learning_scope = str(payload.get("learning_scope") or payload.get("scope") or "")
    learning_source = str(payload.get("learning_source") or "")
    learning_origin = str(payload.get("learning_origin") or "")
    if not learning_origin:
        if source_valkyrie_id:
            learning_origin = "local" if source_valkyrie_id == valkyrie_id else "peer"
        elif learning_source.startswith("flock-learning:") or learning_scope in {
            "flock",
            "shared",
        }:
            learning_origin = "unknown"
        else:
            learning_origin = "local"
    return {
        "skillName": skill_name,
        "environmentId": canonical_environment_id(payload.get("environment_id")),
        "valkyrieId": valkyrie_id,
        "description": str(payload.get("summary_text") or ""),
        "content": str(payload.get("skill_content") or ""),
        "toolCode": str(payload.get("tool_code") or ""),
        "testCode": str(payload.get("test_code") or ""),
        "requirements": [str(entry) for entry in list(payload.get("requirements") or [])],
        "manifest": dict(manifest) if isinstance(manifest, dict) else {},
        "learningId": str(payload.get("learning_id") or ""),
        "learningOrigin": learning_origin,
        "learningScope": learning_scope,
        "learningSource": learning_source,
        "sourceEnvironmentId": str(payload.get("source_environment_id") or ""),
        "sourceValkyrieId": source_valkyrie_id,
        "adoptedAt": adopted_at,
        "observedAt": observed_at,
    }


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {key: value for key, value in record.items() if key not in _SUMMARY_EXCLUDED_KEYS}
    summary["hasCode"] = bool(record["toolCode"])
    return summary


class ValkyrieSkillMirror:
    """Latest activated learned-skill record per (environment, skill name)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}

    async def ingest_event(self, event: SleipnirEvent | dict[str, Any]) -> None:
        """Keep the newest activation per skill; ignore every other event."""
        record = skill_record_from_event(_event_dict(event))
        if record is None:
            return
        key = (record["environmentId"], record["skillName"])
        previous = self._records.get(key)
        if previous is not None and not record["adoptedAt"]:
            record["adoptedAt"] = previous["adoptedAt"]
        self._records[key] = record

    def list(self, environment_id: str) -> list[dict[str, Any]]:
        """Code-free summaries of the environment's learned skills."""
        canonical_id = canonical_environment_id(environment_id)
        return sorted(
            (
                _summary(record)
                for (record_env, _), record in self._records.items()
                if record_env == canonical_id
            ),
            key=lambda summary: summary["skillName"],
        )

    def get(self, environment_id: str, name: str) -> dict[str, Any] | None:
        """Full record — description, markdown, tool code, tests — or None."""
        return self._records.get((canonical_environment_id(environment_id), name))

    def list_all(self) -> list[dict[str, Any]]:
        """Code-free summaries for metrics across every configured environment."""
        return sorted(
            (_summary(record) for record in self._records.values()),
            key=lambda summary: (summary["environmentId"], summary["skillName"]),
        )


def create_valkyrie_skills_router(mirror: ValkyrieSkillMirror) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ravn/valkyrie", tags=["Ravn Valkyries"])

    @router.get("/skills")
    async def list_skills(environment_id: str) -> dict[str, Any]:
        items = mirror.list(environment_id)
        return {"items": items, "total": len(items)}

    @router.get("/skills/{name}")
    async def get_skill(name: str, environment_id: str) -> dict[str, Any]:
        record = mirror.get(environment_id, name)
        if record is None:
            raise HTTPException(status_code=404, detail="Learned skill not found")
        return record

    return router
