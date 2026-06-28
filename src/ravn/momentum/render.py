"""Markdown rendering for persisted momentum artifacts."""

from __future__ import annotations

import json
from datetime import datetime

from ravn.domain.valkyrie_contracts import (
    VALKYRIE_JUDGMENT_PROPOSED,
    normalize_valkyrie_outcome,
    validate_valkyrie_outcome,
)
from ravn.momentum.models import (
    MomentumArtifact,
    MomentumAttentionDecision,
    MomentumExtractionRun,
    MomentumJudgment,
    MomentumJudgmentDisposition,
    MomentumPacket,
    MomentumReflection,
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


def render_disposition(disposition: MomentumJudgmentDisposition) -> str:
    return (
        "# Momentum Judgment Disposition\n\n"
        f"- disposition_id: {disposition.disposition_id}\n"
        f"- target_ref: {disposition.target_ref}\n"
        f"- outcome: {disposition.outcome}\n"
        f"- actor: {disposition.actor}\n"
        f"- source: {disposition.source}\n"
        f"- created_at: {disposition.created_at.isoformat()}\n\n"
        "## Note\n\n"
        f"{disposition.note}\n"
    )


def render_attention_decision(decision: MomentumAttentionDecision) -> str:
    selected = decision.selected_signal_ref or decision.selected_signal_id or "-"
    return (
        f"# Momentum Attention Decision {decision.decision_id}\n\n"
        f"- decision_id: {decision.decision_id}\n"
        f"- selected_signal_id: {decision.selected_signal_id or '-'}\n"
        f"- selected_signal_ref: {decision.selected_signal_ref or '-'}\n"
        f"- no_attention_needed: {str(decision.no_attention_needed).lower()}\n"
        f"- selected: {selected}\n"
        f"- validation_status: {decision.validation_status}\n"
        f"- attention_tier: {decision.attention_tier}\n"
        f"- recommended_next_action: {decision.recommended_next_action}\n"
        f"- confidence: {decision.confidence}\n"
        f"- current_state_ref: {decision.current_state_ref or '-'}\n"
        f"- current_state_present: {str(decision.current_state_present).lower()}\n"
        f"- candidate_count: {decision.candidate_count}\n"
        f"- candidate_limit: {decision.candidate_limit}\n"
        f"- candidates_truncated: {decision.candidates_truncated}\n"
        f"- procedure: {decision.procedure_name}\n"
        f"- model: {decision.model_name}\n"
        f"- created_at: {decision.created_at.isoformat()}\n\n"
        "## Rationale\n\n"
        f"{decision.rationale}\n\n"
        "## Why Attention Now\n\n"
        f"{decision.why_now}\n\n"
        "## Selected Tensions\n\n"
        f"{_bullets_text(decision.selected_tension_ids)}\n\n"
        "## Evidence Refs\n\n"
        f"{_bullets_text(decision.evidence_refs)}\n\n"
        "## Signal Refs\n\n"
        f"{_bullets_text(decision.signal_refs)}\n\n"
        "## Source Refs\n\n"
        f"{_bullets_text(decision.source_refs)}\n"
    )


def parse_attention_decision(content: str) -> MomentumAttentionDecision:
    return MomentumAttentionDecision(
        decision_id=_field(content, "decision_id"),
        selected_signal_id=_optional_field(content, "selected_signal_id"),
        selected_signal_ref=_optional_field(content, "selected_signal_ref"),
        no_attention_needed=_bool_field(content, "no_attention_needed"),
        selected_tension_ids=_section_bullets(content, "Selected Tensions"),
        validation_status=_field(content, "validation_status"),
        attention_tier=_field(content, "attention_tier"),
        rationale=_section_text(content, "Rationale"),
        why_now=_section_text(content, "Why Attention Now"),
        evidence_refs=_section_bullets(content, "Evidence Refs"),
        signal_refs=_section_bullets(content, "Signal Refs"),
        recommended_next_action=_field(content, "recommended_next_action"),
        confidence=float(_field(content, "confidence")),
        source_refs=_section_bullets(content, "Source Refs"),
        created_at=datetime.fromisoformat(_field(content, "created_at")),
        current_state_ref=_optional_field(content, "current_state_ref"),
        current_state_present=_bool_field(content, "current_state_present"),
        candidate_count=int(_field(content, "candidate_count")),
        candidate_limit=int(_field(content, "candidate_limit")),
        candidates_truncated=int(_field(content, "candidates_truncated")),
        procedure_name=_field(content, "procedure"),
        model_name=_field(content, "model"),
    )


def render_reflection(reflection: MomentumReflection) -> str:
    return (
        "# Momentum Judgment Reflection\n\n"
        f"- reflection_id: {reflection.reflection_id}\n"
        f"- target_ref: {reflection.target_ref}\n"
        f"- disposition_ref: {reflection.disposition_ref}\n"
        f"- outcome: {reflection.outcome}\n"
        f"- actor: {reflection.actor}\n"
        f"- procedure: {reflection.procedure_name}\n"
        f"- model: {reflection.model_name}\n"
        f"- reflected_at: {reflection.reflected_at.isoformat()}\n"
        "- candidate_reflex_status: candidate_only\n"
        "- candidate_capability_gap_status: candidate_only\n\n"
        "## Changed Understanding\n\n"
        f"{reflection.changed_understanding}\n\n"
        "## Lesson Learned\n\n"
        f"{reflection.lesson_learned}\n\n"
        "## Original Judgment Useful\n\n"
        f"{str(reflection.original_judgment_useful).lower()}\n\n"
        "## Remember Next Time\n\n"
        f"{_bullets_text(reflection.remember_next_time)}\n\n"
        "## Resident Corrections\n\n"
        f"{_bullets_text(reflection.resident_corrections)}\n\n"
        "## Candidate Reflexes\n\n"
        f"{_bullets_text(reflection.candidate_reflexes)}\n\n"
        "## Candidate Capability Gaps\n\n"
        f"{_bullets_text(reflection.candidate_capability_gaps)}\n\n"
        "## State Patch\n\n"
        f"```json\n{reflection.state_patch.model_dump_json(indent=2)}\n```\n"
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
        f"- input_state_ref: {run.input_state_ref or '-'}\n"
        f"- input_state_sha256: {run.input_state_sha256 or '-'}\n"
        f"- procedure: {run.procedure_name}\n"
        f"- model: {run.model_name}\n"
        f"- created_at: {run.created_at.isoformat()}\n"
        f"- provenance_fully_verified: {str(run.provenance_fully_verified).lower()}\n"
        f"- judgment_ref: {run.judgment_ref}\n"
        f"- packet_ref: {run.packet_ref or '-'}\n"
        f"- attention_ref: {run.attention_ref or '-'}\n"
        f"- attention_decision_id: {run.attention_decision_id or '-'}\n"
        f"- selected_signal_id: {run.selected_signal_id or '-'}\n"
        f"- selected_signal_ref: {run.selected_signal_ref or '-'}\n\n"
        "## Artifact Refs\n\n"
        f"{_bullets_text(run.artifact_refs)}\n"
    )


def _field(content: str, field: str) -> str:
    prefix = f"- {field}:"
    for line in content.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    raise ValueError(f"attention decision missing field: {field}")


def _optional_field(content: str, field: str) -> str | None:
    value = _field(content, field)
    if not value or value == "-":
        return None
    return value


def _bool_field(content: str, field: str) -> bool:
    value = _field(content, field).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"attention decision field is not a boolean: {field}")


def _section_text(content: str, title: str) -> str:
    marker = f"## {title}"
    if marker not in content:
        raise ValueError(f"attention decision missing section: {title}")
    _, tail = content.split(marker, 1)
    body = tail.split("\n## ", 1)[0].strip()
    if not body:
        raise ValueError(f"attention decision section is empty: {title}")
    return body


def _section_bullets(content: str, title: str) -> list[str]:
    items: list[str] = []
    for line in _section_text(content, title).splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped.removeprefix("- ").strip()
        if item and item != "none":
            items.append(item)
    return items


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- none"]


def _bullets_text(items: list[str]) -> str:
    return "\n".join(_bullets(items))
