"""Prometheus projection of installed and exercised Valkyrie skills."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Protocol

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ravn.api.valkyrie_skills import ValkyrieSkillMirror

_DISPLAY_WORDS = {
    "backofflimitexceeded": "Backoff Limit Exceeded",
    "clustersecretstore": "ClusterSecretStore",
    "cnpg": "CNPG",
    "cronjob": "CronJob",
    "daemonset": "DaemonSet",
    "deadlineexceeded": "Deadline Exceeded",
    "failedattachvolume": "Failed Attach Volume",
    "failedmount": "Failed Mount",
    "k8s": "K8s",
    "oidc": "OIDC",
    "openbao": "OpenBao",
    "outofcpu": "OutOfCPU",
    "pvc": "PVC",
    "replicaset": "ReplicaSet",
    "statefulset": "StatefulSet",
}


class SkillStatsReader(Protocol):
    async def skill_stats(self, *, environment_id: str = "") -> list[dict[str, Any]]: ...


def _escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**values: Any) -> str:
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in values.items()) + "}"


def _display_name(value: Any) -> str:
    name = str(value).strip()
    if not name or " " in name:
        return name
    name = re.sub(
        r"^(?:valkyrie[-_.])?inspect[-_.](?:kubernetes|k8s)[-_.]",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"^valkyrie[-_.]", "", name, flags=re.IGNORECASE)
    return " ".join(
        _DISPLAY_WORDS.get(word.lower(), word.capitalize()) for word in re.split(r"[-_.]+", name)
    )


def _timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def render_valkyrie_skill_metrics(
    skills: list[dict[str, Any]],
    stats: list[dict[str, Any]],
) -> str:
    """Render durable skill inventory and judgment-backed use totals."""
    lines = [
        "# HELP ravn_valkyrie_skill_installed Installed skill inventory (1 = present).",
        "# TYPE ravn_valkyrie_skill_installed gauge",
    ]
    for skill in skills:
        labels = _labels(
            environment_id=skill.get("environmentId", ""),
            valkyrie_id=skill.get("valkyrieId", ""),
            skill_name=skill.get("skillName", ""),
            skill_display_name=_display_name(skill.get("skillName", "")),
            has_code=str(bool(skill.get("hasCode"))).lower(),
            learning_origin=skill.get("learningOrigin", "unknown"),
            learning_scope=skill.get("learningScope", ""),
            source_environment_id=skill.get("sourceEnvironmentId", ""),
            source_valkyrie_id=skill.get("sourceValkyrieId", ""),
        )
        lines.append(f"ravn_valkyrie_skill_installed{labels} 1")

    metric_fields = (
        ("uses", "uses", "Judgment-backed uses of an installed skill."),
        ("successes", "successes", "Successful judgment-backed skill uses."),
        ("failures", "failures", "Failed or regressed judgment-backed skill uses."),
    )
    for suffix, field, help_text in metric_fields:
        name = f"ravn_valkyrie_skill_{suffix}"
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        for stat in stats:
            labels = _labels(
                environment_id=stat.get("environmentId", ""),
                skill_name=stat.get("skillName", ""),
                skill_display_name=_display_name(stat.get("skillName", "")),
                capability=stat.get("capability", ""),
            )
            lines.append(f"{name}{labels} {int(stat.get(field) or 0)}")

    lines.extend(
        (
            "# HELP ravn_valkyrie_skill_last_used_timestamp_seconds "
            "Unix timestamp of the latest judgment-backed skill use.",
            "# TYPE ravn_valkyrie_skill_last_used_timestamp_seconds gauge",
        )
    )
    for stat in stats:
        labels = _labels(
            environment_id=stat.get("environmentId", ""),
            skill_name=stat.get("skillName", ""),
            skill_display_name=_display_name(stat.get("skillName", "")),
            capability=stat.get("capability", ""),
        )
        lines.append(
            "ravn_valkyrie_skill_last_used_timestamp_seconds"
            f"{labels} {_timestamp(stat.get('lastUsedAt')):g}"
        )
    return "\n".join(lines) + "\n"


def create_valkyrie_metrics_router(
    mirror: ValkyrieSkillMirror,
    history: SkillStatsReader,
) -> APIRouter:
    router = APIRouter(tags=["Ravn Valkyries"])

    @router.get("/api/v1/ravn/metrics", response_class=PlainTextResponse)
    async def metrics() -> PlainTextResponse:
        body = render_valkyrie_skill_metrics(mirror.list_all(), await history.skill_stats())
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    return router
