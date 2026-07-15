"""Prometheus metrics for installed and exercised Valkyrie skills."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.api.valkyrie_metrics import create_valkyrie_metrics_router, render_valkyrie_skill_metrics
from ravn.api.valkyrie_skills import ValkyrieSkillMirror


def _skills() -> list[dict[str, Any]]:
    return [
        {
            "environmentId": "env-k8s-ymir",
            "valkyrieId": "valkyrie-ymir-k8s",
            "skillName": 'Detect "OIDC"',
            "hasCode": False,
            "learningOrigin": "peer",
            "learningScope": "environment",
            "sourceEnvironmentId": "env-k8s-valhalla",
            "sourceValkyrieId": "valkyrie-valhalla-k8s",
        }
    ]


def _stats() -> list[dict[str, Any]]:
    return [
        {
            "environmentId": "env-k8s-ymir",
            "skillName": 'Detect "OIDC"',
            "capability": "inspect.kubernetes.job",
            "uses": 3,
            "successes": 2,
            "failures": 1,
            "lastUsedAt": "2026-07-15T10:00:00+00:00",
        }
    ]


def test_render_metrics_exposes_inventory_and_judgment_backed_usage() -> None:
    body = render_valkyrie_skill_metrics(_skills(), _stats())

    assert 'skill_name="Detect \\"OIDC\\""' in body
    assert 'has_code="false"' in body
    assert 'learning_origin="peer"' in body
    assert 'source_valkyrie_id="valkyrie-valhalla-k8s"' in body
    assert "ravn_valkyrie_skill_uses{" in body
    assert "} 3" in body
    assert "ravn_valkyrie_skill_successes{" in body
    assert "ravn_valkyrie_skill_failures{" in body
    assert "ravn_valkyrie_skill_last_used_timestamp_seconds{" in body


def test_render_metrics_handles_absent_usage_without_inventing_it() -> None:
    body = render_valkyrie_skill_metrics(_skills(), [])

    assert "ravn_valkyrie_skill_installed{" in body
    assert "ravn_valkyrie_skill_uses{" not in body


def test_metrics_endpoint_reads_current_mirror_and_history() -> None:
    class History:
        async def skill_stats(self, *, environment_id: str = "") -> list[dict[str, Any]]:
            assert environment_id == ""
            return _stats()

    mirror = ValkyrieSkillMirror()
    mirror._records[("env-k8s-ymir", 'Detect "OIDC"')] = {  # noqa: SLF001
        **_skills()[0],
        "description": "OIDC failure pattern",
        "content": "# Skill",
        "toolCode": "",
        "testCode": "",
        "requirements": [],
        "manifest": {},
        "learningId": "learning-1",
        "adoptedAt": "2026-06-13T00:00:00+00:00",
        "observedAt": "2026-07-15T10:00:00+00:00",
    }
    app = FastAPI()
    app.include_router(create_valkyrie_metrics_router(mirror, History()))

    response = TestClient(app).get("/api/v1/ravn/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'environment_id="env-k8s-ymir"' in response.text
    assert "ravn_valkyrie_skill_uses{" in response.text
