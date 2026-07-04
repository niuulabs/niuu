"""Dashboard-side mirror of learned skills installed by resident Valkyries.

A judgment only ever references a learned skill by NAME while the skill body
lives on the resident's disk. The ``valkyrie.evolution.activated`` event now
carries the full artifact (markdown content, tool code, tests, requirements),
so this module keeps the latest activated version per environment and serves
it to the dashboard — letting an operator open "handled with learned skill X"
and actually read X. Like :class:`ValkyrieDashboardProjection`, the mirror is
in-memory and rebuilds from the telemetry stream replay after a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ravn.domain.valkyrie_history import canonical_environment_id
from ravn.valkyrie_evolution.resident_learning import EVOLUTION_ACTIVATED_EVENT
from sleipnir.domain.events import SleipnirEvent

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
    """Return a learned-skill record for an activation event; None otherwise."""
    if str(event.get("event_type") or "") != EVOLUTION_ACTIVATED_EVENT:
        return None
    payload = _payload(event)
    skill_name = str(payload.get("skill_name") or "").strip()
    if not skill_name:
        return None
    manifest = payload.get("learned_tool_manifest")
    return {
        "skillName": skill_name,
        "environmentId": canonical_environment_id(payload.get("environment_id")),
        "valkyrieId": str(payload.get("valkyrie_id") or ""),
        "description": str(payload.get("summary_text") or ""),
        "content": str(payload.get("skill_content") or ""),
        "toolCode": str(payload.get("tool_code") or ""),
        "testCode": str(payload.get("test_code") or ""),
        "requirements": [str(entry) for entry in list(payload.get("requirements") or [])],
        "manifest": dict(manifest) if isinstance(manifest, dict) else {},
        "learningId": str(payload.get("learning_id") or ""),
        "adoptedAt": _timestamp(event),
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
        self._records[(record["environmentId"], record["skillName"])] = record

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
