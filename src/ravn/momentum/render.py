"""Markdown rendering for persisted momentum artifacts."""

from __future__ import annotations

import json

from ravn.domain.valkyrie_contracts import (
    VALKYRIE_JUDGMENT_PROPOSED,
    normalize_valkyrie_outcome,
    validate_valkyrie_outcome,
)
from ravn.momentum.models import (
    MomentumArtifact,
    MomentumExtractionRun,
    MomentumJudgment,
    MomentumPacket,
    ResidentUnderstandingPatch,
)


def render_artifact(artifact: MomentumArtifact | ResidentUnderstandingPatch) -> str:
    lines = [
        f"# {artifact.title}",
        "",
        f"- kind: {artifact.kind}",
        f"- artifact_id: {artifact.artifact_id}",
        f"- signal_kind: {artifact.provenance.signal_kind}",
        f"- extraction_run_id: {artifact.provenance.extraction_run_id}",
        f"- procedure: {artifact.provenance.procedure_name}",
        f"- model: {artifact.provenance.model_name}",
        f"- source_path: {artifact.provenance.source_path}",
        f"- source_sha256: {artifact.provenance.source_sha256}",
        f"- extracted_at: {artifact.provenance.extracted_at.isoformat()}",
        f"- line_start: {artifact.provenance.line_start or '-'}",
        f"- line_end: {artifact.provenance.line_end or '-'}",
        f"- provenance_status: {artifact.provenance.verification_status}",
        f"- provenance_reason: {artifact.provenance.verification_reason}",
        "",
        "## Summary",
        "",
        artifact.summary,
        "",
        "## Why It Matters",
        "",
        artifact.reason,
        "",
        "## Source Context",
        "",
        artifact.provenance.source_excerpt,
    ]
    if isinstance(artifact, ResidentUnderstandingPatch):
        lines.extend(
            [
                "",
                "## Beliefs",
                "",
                *_bullets(artifact.beliefs),
                "",
                "## Constraints",
                "",
                *_bullets(artifact.constraints),
                "",
                "## Corrections",
                "",
                *_bullets(artifact.corrections),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_packet(packet: MomentumPacket) -> str:
    return (
        f"# {packet.title}\n\n"
        f"- packet_id: {packet.packet_id}\n"
        f"- signal_kind: {packet.provenance.signal_kind}\n"
        f"- extraction_run_id: {packet.provenance.extraction_run_id}\n"
        f"- procedure: {packet.provenance.procedure_name}\n"
        f"- model: {packet.provenance.model_name}\n"
        f"- source_path: {packet.provenance.source_path}\n"
        f"- source_sha256: {packet.provenance.source_sha256}\n"
        f"- extracted_at: {packet.provenance.extracted_at.isoformat()}\n"
        f"- line_start: {packet.provenance.line_start or '-'}\n"
        f"- line_end: {packet.provenance.line_end or '-'}\n"
        f"- provenance_status: {packet.provenance.verification_status}\n"
        f"- provenance_reason: {packet.provenance.verification_reason}\n\n"
        "## Implementation Slice\n\n"
        f"{packet.implementation_slice}\n\n"
        "## Why It Matters\n\n"
        f"{packet.why_it_matters}\n\n"
        "## Caused By\n\n"
        f"{_bullets_text(packet.caused_by)}\n\n"
        "## Must Not Lose\n\n"
        f"{_bullets_text(packet.must_not_lose)}\n\n"
        "## Reuse Guidance\n\n"
        f"{_bullets_text(packet.reuse_guidance)}\n\n"
        "## Out Of Scope\n\n"
        f"{_bullets_text(packet.out_of_scope)}\n\n"
        "## Success Proof\n\n"
        f"{packet.success_proof}\n\n"
        "## Reflection Prompts\n\n"
        f"{_bullets_text(packet.reflection_prompts)}\n\n"
        "## Source Context\n\n"
        f"{packet.provenance.source_excerpt}\n"
    )


def render_judgment(judgment: MomentumJudgment) -> str:
    payload = judgment_event_payload(judgment)
    return (
        f"# {judgment.title}\n\n"
        f"- event_type: {judgment.event_type}\n"
        f"- judgment_id: {judgment.judgment_id}\n"
        f"- environment_id: {judgment.environment_id}\n"
        f"- valkyrie_id: {judgment.valkyrie_id}\n"
        f"- signal_kind: {judgment.provenance.signal_kind}\n"
        f"- extraction_run_id: {judgment.provenance.extraction_run_id}\n"
        f"- procedure: {judgment.provenance.procedure_name}\n"
        f"- model: {judgment.provenance.model_name}\n"
        f"- source_path: {judgment.provenance.source_path}\n"
        f"- source_sha256: {judgment.provenance.source_sha256}\n"
        f"- extracted_at: {judgment.provenance.extracted_at.isoformat()}\n"
        f"- attention_tier: {judgment.attention_tier}\n"
        f"- confidence: {judgment.confidence}\n"
        f"- authority_boundary: {judgment.authority_boundary}\n"
        f"- recommended_next_action: {judgment.recommended_next_action}\n"
        f"- provenance_status: {judgment.provenance.verification_status}\n"
        f"- provenance_reason: {judgment.provenance.verification_reason}\n\n"
        "## Changed Understanding\n\n"
        f"{judgment.changed_understanding}\n\n"
        "## Tension That Matters\n\n"
        f"{judgment.tension_that_matters}\n\n"
        "## Why Attention Now\n\n"
        f"{judgment.why_attention_now}\n\n"
        "## Recommended Action\n\n"
        f"{judgment.recommended_action}\n\n"
        "## Evidence Artifacts\n\n"
        f"{_bullets_text(judgment.evidence_artifact_titles)}\n\n"
        "## Valkyrie Judgment Payload\n\n"
        f"```json\n{json.dumps(payload, indent=2, sort_keys=True)}\n```\n\n"
        "## Source Context\n\n"
        f"{judgment.provenance.source_excerpt}\n"
    )


def judgment_event_payload(judgment: MomentumJudgment) -> dict[str, object]:
    payload = normalize_valkyrie_outcome(
        VALKYRIE_JUDGMENT_PROPOSED,
        {
            "event_type": judgment.event_type,
            "environment_id": judgment.environment_id,
            "valkyrie_id": judgment.valkyrie_id,
            "signal_refs": list(judgment.signal_refs),
            "tier": judgment.attention_tier,
            "confidence": judgment.confidence,
            "operational_state": judgment.operational_state,
            "rationale": judgment.why_attention_now,
            "evidence": [
                {
                    "kind": "momentum_artifact",
                    "title": title,
                    "run_id": judgment.provenance.extraction_run_id,
                }
                for title in judgment.evidence_artifact_titles
            ],
            "recommended_action": judgment.recommended_action,
            "action_authority": judgment.authority_boundary,
            "target_surfaces": list(judgment.target_surfaces),
            "expires_at": "",
            "dissent_refs": [],
            "correlation_ids": {
                "root": judgment.provenance.extraction_run_id,
                "source": judgment.provenance.source_path,
            },
        },
    )
    errors = validate_valkyrie_outcome(VALKYRIE_JUDGMENT_PROPOSED, payload)
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def render_run(run: MomentumExtractionRun) -> str:
    return (
        f"# Resident Signal Momentum Run {run.run_id}\n\n"
        f"- signal_kind: {run.signal_kind}\n"
        f"- source_path: {run.source_path}\n"
        f"- source_sha256: {run.source_sha256}\n"
        f"- procedure: {run.procedure_name}\n"
        f"- model: {run.model_name}\n"
        f"- created_at: {run.created_at.isoformat()}\n"
        f"- provenance_fully_verified: {str(run.provenance_fully_verified).lower()}\n"
        f"- judgment_ref: {run.judgment_ref}\n"
        f"- packet_ref: {run.packet_ref or '-'}\n\n"
        "## Artifact Refs\n\n"
        f"{_bullets_text(run.artifact_refs)}\n"
    )


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- none"]


def _bullets_text(items: list[str]) -> str:
    return "\n".join(_bullets(items))
