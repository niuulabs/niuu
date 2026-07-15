"""Dashboard learned-skill mirror + API tests (view the learned skill)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.api.valkyrie_skills import (
    ValkyrieSkillMirror,
    create_valkyrie_skills_router,
    skill_record_from_event,
)
from sleipnir.domain.events import SleipnirEvent

_SKILL_NAME = "valkyrie-inspect-kubernetes-pod-oomkilled"
_TOOL_CODE = 'def run(payload):\n    return {"ok": True}\n'
_TEST_CODE = "def test_run():\n    assert True\n"


def _activation_event(**payload_overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "environment_id": "valhalla",
        "valkyrie_id": "valkyrie:valhalla",
        "learning_id": "learn-k8s-oom",
        "promotion_id": "promo-k8s-oom",
        "skill_name": _SKILL_NAME,
        "artifact_type": "tool_skill",
        "scope": "flock",
        "source_environment_id": "cluster-source",
        "source_valkyrie_id": "valkyrie:source",
        "review_outcome": "approve",
        "autonomy_mode": "guarded",
        "skill_content": f"# skill: {_SKILL_NAME}\n\nInspect pod events first.\n",
        "learned_tool_manifest": {"capability": "inspect.kubernetes.pod.oomkilled"},
        "tool_code": _TOOL_CODE,
        "test_code": _TEST_CODE,
        "requirements": ["kubernetes==29.0.0"],
        "summary_text": "Use pod events and node pressure before restarting OOMKilled pods.",
    }
    payload.update(payload_overrides)
    return {
        "event_id": "evt-activated-1",
        "event_type": "valkyrie.evolution.activated",
        "timestamp": "2026-07-03T08:00:00+00:00",
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------


def test_skill_record_canonicalizes_environment_and_carries_artifact() -> None:
    record = skill_record_from_event(_activation_event())

    assert record == {
        "skillName": _SKILL_NAME,
        "environmentId": "env-k8s-valhalla",
        "valkyrieId": "valkyrie:valhalla",
        "description": "Use pod events and node pressure before restarting OOMKilled pods.",
        "content": f"# skill: {_SKILL_NAME}\n\nInspect pod events first.\n",
        "toolCode": _TOOL_CODE,
        "testCode": _TEST_CODE,
        "requirements": ["kubernetes==29.0.0"],
        "manifest": {"capability": "inspect.kubernetes.pod.oomkilled"},
        "learningId": "learn-k8s-oom",
        "learningOrigin": "peer",
        "learningScope": "flock",
        "learningSource": "",
        "sourceEnvironmentId": "cluster-source",
        "sourceValkyrieId": "valkyrie:source",
        "adoptedAt": "2026-07-03T08:00:00+00:00",
        "observedAt": "2026-07-03T08:00:00+00:00",
    }


def test_skill_record_none_for_other_events_and_missing_name() -> None:
    other = _activation_event()
    other["event_type"] = "valkyrie.judgment.proposed"

    assert skill_record_from_event(other) is None
    assert skill_record_from_event(_activation_event(skill_name="")) is None
    assert skill_record_from_event(_activation_event(skill_name="   ")) is None
    assert skill_record_from_event({"event_type": "valkyrie.evolution.activated"}) is None


def _inventory_event(**payload_overrides: Any) -> dict[str, Any]:
    event = _activation_event(**payload_overrides)
    event["event_id"] = "evt-inventory-1"
    event["event_type"] = "valkyrie.evolution.skill_inventory"
    event["payload"]["status"] = "present"
    return event


def test_skill_record_handles_inventory_event_and_canonicalizes_env() -> None:
    record = skill_record_from_event(_inventory_event())

    assert record is not None
    # Same extraction as activation: the inventory snapshot carries the full
    # enriched record so the dashboard can render the skill body.
    assert record["skillName"] == _SKILL_NAME
    assert record["environmentId"] == "env-k8s-valhalla"
    assert record["content"] == f"# skill: {_SKILL_NAME}\n\nInspect pod events first.\n"
    assert record["toolCode"] == _TOOL_CODE
    assert record["testCode"] == _TEST_CODE
    assert record["requirements"] == ["kubernetes==29.0.0"]
    assert record["manifest"] == {"capability": "inspect.kubernetes.pod.oomkilled"}
    assert record["adoptedAt"] == ""
    assert record["observedAt"] == "2026-07-03T08:00:00+00:00"
    assert record["learningOrigin"] == "peer"


def test_skill_record_marks_old_shared_learning_provenance_unknown() -> None:
    record = skill_record_from_event(
        _inventory_event(source_environment_id="", source_valkyrie_id="")
    )

    assert record is not None
    assert record["learningOrigin"] == "unknown"


def test_inventory_uses_durable_adoption_timestamp() -> None:
    record = skill_record_from_event(_inventory_event(adopted_at="2026-06-13T09:15:00+00:00"))

    assert record is not None
    assert record["adoptedAt"] == "2026-06-13T09:15:00+00:00"
    assert record["observedAt"] == "2026-07-03T08:00:00+00:00"


def test_skill_record_defaults_for_pre_enrichment_events() -> None:
    event = _activation_event()
    for key in (
        "skill_content",
        "learned_tool_manifest",
        "tool_code",
        "test_code",
        "requirements",
        "summary_text",
    ):
        del event["payload"][key]
    del event["timestamp"]

    record = skill_record_from_event(event)

    assert record is not None
    assert record["description"] == ""
    assert record["content"] == ""
    assert record["toolCode"] == ""
    assert record["testCode"] == ""
    assert record["requirements"] == []
    assert record["manifest"] == {}
    assert record["adoptedAt"]
    assert record["observedAt"]


def test_skill_record_formats_datetime_timestamps() -> None:
    event = _activation_event()
    event["timestamp"] = datetime(2026, 7, 3, 9, 30, tzinfo=UTC)

    record = skill_record_from_event(event)

    assert record is not None
    assert record["adoptedAt"] == "2026-07-03T09:30:00+00:00"


# ---------------------------------------------------------------------------
# Mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_keeps_latest_record_per_skill() -> None:
    mirror = ValkyrieSkillMirror()
    await mirror.ingest_event(_activation_event(summary_text="v1"))
    await mirror.ingest_event(_activation_event(summary_text="v2", skill_content="# skill v2\n"))
    await mirror.ingest_event(_activation_event(skill_name="valkyrie-drain-node"))
    await mirror.ingest_event({"event_type": "valkyrie.signal.detected", "payload": {}})

    record = mirror.get("env-k8s-valhalla", _SKILL_NAME)
    assert record is not None
    assert record["description"] == "v2"
    assert record["content"] == "# skill v2\n"
    names = [item["skillName"] for item in mirror.list("env-k8s-valhalla")]
    assert names == ["valkyrie-drain-node", _SKILL_NAME]


@pytest.mark.asyncio
async def test_mirror_populated_by_inventory_event_and_served() -> None:
    # The core fix: a resident that never re-adopts still lands in the mirror
    # via the periodic inventory snapshot, so list/get serve the skill.
    mirror = ValkyrieSkillMirror()
    await mirror.ingest_event(_inventory_event())

    record = mirror.get("env-k8s-valhalla", _SKILL_NAME)
    assert record is not None
    assert record["content"].startswith(f"# skill: {_SKILL_NAME}")
    assert record["toolCode"] == _TOOL_CODE
    names = [item["skillName"] for item in mirror.list("env-k8s-valhalla")]
    assert names == [_SKILL_NAME]


@pytest.mark.asyncio
async def test_mirror_latest_wins_across_activation_and_inventory() -> None:
    # Whichever record arrived most recently wins, regardless of which event
    # type carried it — correct semantics for a live inventory.
    mirror = ValkyrieSkillMirror()
    await mirror.ingest_event(_activation_event(summary_text="from-activation"))
    await mirror.ingest_event(_inventory_event(summary_text="from-inventory"))

    record = mirror.get("env-k8s-valhalla", _SKILL_NAME)
    assert record is not None
    assert record["description"] == "from-inventory"
    assert record["adoptedAt"] == "2026-07-03T08:00:00+00:00"


@pytest.mark.asyncio
async def test_mirror_accepts_sleipnir_events_and_raw_environment_ids() -> None:
    mirror = ValkyrieSkillMirror()
    await mirror.ingest_event(
        SleipnirEvent(
            event_type="valkyrie.evolution.activated",
            source="ravn:test",
            payload=_activation_event()["payload"],
            summary="installed",
            urgency=0.25,
            domain="infrastructure",
            timestamp=datetime(2026, 7, 3, 8, 0, tzinfo=UTC),
        )
    )

    # Queries with the resident's raw environment id hit the same record.
    assert mirror.get("valhalla", _SKILL_NAME) is not None
    assert mirror.list("valhalla")[0]["environmentId"] == "env-k8s-valhalla"
    assert mirror.get("env-k8s-valhalla", "missing") is None
    assert mirror.list("env-k8s-other") == []


@pytest.mark.asyncio
async def test_list_summaries_exclude_code_and_flag_it() -> None:
    mirror = ValkyrieSkillMirror()
    await mirror.ingest_event(_activation_event())
    await mirror.ingest_event(
        _activation_event(skill_name="valkyrie-markdown-only", tool_code="", test_code="")
    )

    summaries = {item["skillName"]: item for item in mirror.list("env-k8s-valhalla")}
    with_code = summaries[_SKILL_NAME]
    without_code = summaries["valkyrie-markdown-only"]

    for summary in (with_code, without_code):
        for hidden in ("content", "toolCode", "testCode", "requirements", "manifest"):
            assert hidden not in summary
    assert with_code["hasCode"] is True
    assert without_code["hasCode"] is False
    assert with_code["description"].startswith("Use pod events")
    assert with_code["adoptedAt"] == "2026-07-03T08:00:00+00:00"


# ---------------------------------------------------------------------------
# Telemetry wiring
# ---------------------------------------------------------------------------


def _activation_sleipnir_event() -> SleipnirEvent:
    return SleipnirEvent(
        event_type="valkyrie.evolution.activated",
        source="ravn:test",
        payload=_activation_event()["payload"],
        summary="installed",
        urgency=0.25,
        domain="infrastructure",
        timestamp=datetime(2026, 7, 3, 8, 0, tzinfo=UTC),
    )


def _subscription(skills_ingest: Any) -> Any:
    from ravn.api.valkyries import (
        ValkyrieDashboardProjection,
        ValkyrieTelemetrySubscription,
    )

    return ValkyrieTelemetrySubscription(
        projection=ValkyrieDashboardProjection(),
        subscribers=[],
        event_types=["*"],
        skills_ingest=skills_ingest,
    )


@pytest.mark.asyncio
async def test_telemetry_handle_feeds_skill_mirror() -> None:
    mirror = ValkyrieSkillMirror()
    subscription = _subscription(mirror.ingest_event)

    await subscription._handle(_activation_sleipnir_event())

    assert mirror.get("valhalla", _SKILL_NAME) is not None


@pytest.mark.asyncio
async def test_telemetry_handle_survives_skill_ingest_failure() -> None:
    async def broken_ingest(event: Any) -> None:
        del event
        raise RuntimeError("mirror down")

    subscription = _subscription(broken_ingest)

    await subscription._handle(_activation_sleipnir_event())


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def _client() -> tuple[TestClient, ValkyrieSkillMirror]:
    mirror = ValkyrieSkillMirror()
    app = FastAPI()
    app.include_router(create_valkyrie_skills_router(mirror))
    return TestClient(app), mirror


def test_skills_endpoints_serve_mirror_records() -> None:
    client, mirror = _client()
    asyncio.run(mirror.ingest_event(_activation_event()))
    base = "/api/v1/ravn/valkyrie/skills"

    listing = client.get(base, params={"environment_id": "env-k8s-valhalla"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["skillName"] == _SKILL_NAME
    assert body["items"][0]["hasCode"] is True
    assert "content" not in body["items"][0]

    detail = client.get(f"{base}/{_SKILL_NAME}", params={"environment_id": "env-k8s-valhalla"})
    assert detail.status_code == 200
    record = detail.json()
    assert record["content"].startswith(f"# skill: {_SKILL_NAME}")
    assert record["toolCode"] == _TOOL_CODE
    assert record["testCode"] == _TEST_CODE
    assert record["requirements"] == ["kubernetes==29.0.0"]
    assert record["manifest"] == {"capability": "inspect.kubernetes.pod.oomkilled"}


def test_skills_endpoints_404_and_require_environment_id() -> None:
    client, _mirror = _client()
    base = "/api/v1/ravn/valkyrie/skills"

    missing = client.get(f"{base}/nope", params={"environment_id": "env-k8s-valhalla"})
    assert missing.status_code == 404
    assert client.get(base).status_code == 422
    assert client.get(f"{base}/nope").status_code == 422
