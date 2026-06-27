"""Markdown rendering for persisted momentum artifacts."""

from __future__ import annotations

from ravn.momentum.models import (
    MomentumArtifact,
    MomentumExtractionRun,
    MomentumPacket,
    ResidentUnderstandingPatch,
)


def render_artifact(artifact: MomentumArtifact | ResidentUnderstandingPatch) -> str:
    lines = [
        f"# {artifact.title}",
        "",
        f"- kind: {artifact.kind}",
        f"- artifact_id: {artifact.artifact_id}",
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


def render_run(run: MomentumExtractionRun) -> str:
    return (
        f"# Momentum Extraction Run {run.run_id}\n\n"
        f"- source_path: {run.source_path}\n"
        f"- source_sha256: {run.source_sha256}\n"
        f"- procedure: {run.procedure_name}\n"
        f"- model: {run.model_name}\n"
        f"- created_at: {run.created_at.isoformat()}\n"
        f"- provenance_fully_verified: {str(run.provenance_fully_verified).lower()}\n"
        f"- packet_ref: {run.packet_ref}\n\n"
        "## Artifact Refs\n\n"
        f"{_bullets_text(run.artifact_refs)}\n"
    )


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- none"]


def _bullets_text(items: list[str]) -> str:
    return "\n".join(_bullets(items))
