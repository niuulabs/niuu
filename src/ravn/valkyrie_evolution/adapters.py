"""Adapters for local and production Valkyrie evolution proof runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest, ReviewResult
from ravn.valkyrie_evolution.ports import (
    EventLedgerPort,
    EvolutionBuilderPort,
    EvolutionReviewPort,
)
from sleipnir.domain.events import SleipnirEvent


class JsonlEventLedger(EventLedgerPort):
    """JSONL-backed event ledger used by the local proof command."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[SleipnirEvent] = []

    async def record(self, event: SleipnirEvent) -> None:
        self._events.append(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    async def list_events(self) -> list[SleipnirEvent]:
        return list(self._events)


class LocalSkillBuilderAdapter(EvolutionBuilderPort):
    """Builds Ravn skill artifacts locally from generic capability gaps."""

    def __init__(self, *, artifact_dir: str | Path | None = None) -> None:
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    async def build(self, request: EvolutionRequest) -> BuildResult:
        gap = request.gap
        skill_name = _skill_name_from_capability(gap.capability_name)
        interesting_fields = _interesting_fields(gap.evidence)
        content = _render_skill(
            skill_name=skill_name,
            capability_name=gap.capability_name,
            gap_reason=gap.reason,
            fields=interesting_fields,
            safety_class=gap.safety_class,
        )
        description = f"Inspect signals requiring {gap.capability_name}."
        artifact_path = ""
        if self.artifact_dir is not None:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.artifact_dir / f"{skill_name}.md"
            artifact.write_text(content, encoding="utf-8")
            artifact_path = str(artifact)
        return BuildResult(
            request_id=request.request_id,
            skill_name=skill_name,
            skill_content=content,
            description=description,
            artifact_type="ravn_skill_tool",
            artifact_path=artifact_path,
            evidence={
                "capability_name": gap.capability_name,
                "derived_from_signal_fields": sorted(interesting_fields),
                "builder": self.__class__.__name__,
            },
        )


class VolundrEvolutionBuilderAdapter(EvolutionBuilderPort):
    """Production adapter boundary for offloading evolution work to Volundr."""

    def __init__(self, *, endpoint: str = "", profile: str = "valkyrie-evolution") -> None:
        self.endpoint = endpoint
        self.profile = profile

    async def build(self, request: EvolutionRequest) -> BuildResult:
        raise NotImplementedError(
            "VolundrEvolutionBuilderAdapter defines the production boundary. "
            "Wire it to Volundr session orchestration before selecting it."
        )


class LocalOdinReviewAdapter(EvolutionReviewPort):
    """Local Odin-court style review gate for generated evolution artifacts."""

    def __init__(self, *, reviewer: str = "odin:local-court") -> None:
        self.reviewer = reviewer

    async def review(
        self,
        *,
        request: EvolutionRequest,
        build: BuildResult,
        autonomy_mode: str,
    ) -> ReviewResult:
        required = autonomy_mode.lower() != "yolo"
        findings = _review_findings(request, build)
        approved = not findings
        if approved:
            outcome = "approved"
            rationale = "Artifact is scoped, read-only, and declares a capability marker."
        else:
            outcome = "rejected" if required else "observed"
            rationale = "Artifact failed local Odin activation checks."
        return ReviewResult(
            request_id=request.request_id,
            artifact_name=build.skill_name,
            approved=approved,
            outcome=outcome,
            rationale=rationale,
            reviewer=self.reviewer,
            required_for_activation=required,
            findings=findings,
        )


def _skill_name_from_capability(capability_name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", capability_name.lower()).strip("-")
    return f"valkyrie-{safe}"


def _review_findings(request: EvolutionRequest, build: BuildResult) -> list[str]:
    findings: list[str] = []
    if build.artifact_type != "ravn_skill_tool":
        findings.append(f"unexpected artifact type: {build.artifact_type}")
    if request.target_scope not in {"private", "environment", "domain"}:
        findings.append(f"scope requires human review: {request.target_scope}")
    if request.gap.safety_class != "read_only":
        findings.append(f"non-read-only safety class: {request.gap.safety_class}")
    capability_marker = f"capability: {request.gap.capability_name}"
    if capability_marker not in build.skill_content:
        findings.append("missing capability marker")
    lower = build.skill_content.lower()
    for blocked in ["rm -rf", "kubectl delete", "send email", "delete secret"]:
        if blocked in lower:
            findings.append(f"blocked operation mentioned: {blocked}")
    return findings


def _interesting_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "kind",
            "reason",
            "object",
            "namespace",
            "host",
            "service",
            "metric",
            "printer",
            "material",
            "threshold",
            "observed",
            "message",
        }
        and value not in ("", None)
    }


def _render_skill(
    *,
    skill_name: str,
    capability_name: str,
    gap_reason: str,
    fields: dict[str, Any],
    safety_class: str,
) -> str:
    field_lines = "\n".join(f"- {key}: `{value}`" for key, value in sorted(fields.items()))
    if not field_lines:
        field_lines = "- No stable fields were present; inspect the raw signal payload."
    return f"""# skill: {skill_name}

Reusable resident Valkyrie capability for `{capability_name}`.

metadata:
  capability: {capability_name}
  source: valkyrie-dream-cycle
  safety_class: {safety_class}

## When To Use

Use this skill when an incoming Environment signal matches capability `{capability_name}`.

## What To Inspect

{field_lines}

## Procedure

1. Re-read the current signal payload and compare it with the learned fields above.
2. Gather read-only context that explains `{gap_reason}` before proposing action.
3. Produce a structured judgment with confidence, evidence, and escalation need.
4. If the signal is still ambiguous, declare the missing context as a new capability gap.
"""
