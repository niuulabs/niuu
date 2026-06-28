"""Small checks for opt-in real LLM momentum evals."""

from __future__ import annotations

from ravn.momentum.models import MomentumExtraction
from ravn.momentum.render import judgment_event_payload


def evaluate_extraction(extraction: MomentumExtraction) -> list[str]:
    errors: list[str] = []
    artifacts = extraction.artifacts
    kinds = {artifact.kind for artifact in artifacts}
    for kind in ("durable_insight", "rejected_direction", "unresolved_tension"):
        if kind not in kinds:
            errors.append(f"missing {kind}")
    if not extraction.resident_patch.reason.strip():
        errors.append("resident patch is missing a reason")
    if not extraction.judgment.changed_understanding.strip():
        errors.append("judgment is missing changed understanding")
    if not extraction.judgment.tension_that_matters.strip():
        errors.append("judgment is missing attention tension")
    if not extraction.judgment.why_attention_now.strip():
        errors.append("judgment is missing attention rationale")
    if extraction.judgment.recommended_next_action == "write_momentum_packet":
        if extraction.packet is None:
            errors.append("judgment recommends a packet but packet is missing")
    payload = judgment_event_payload(extraction.judgment)
    for payload_field in (
        "event_type",
        "environment_id",
        "valkyrie_id",
        "signal_refs",
        "tier",
        "confidence",
        "operational_state",
        "rationale",
        "evidence",
        "recommended_action",
        "action_authority",
        "target_surfaces",
        "correlation_ids",
    ):
        if payload_field not in payload:
            errors.append(f"judgment payload missing {payload_field}")
    if extraction.packet is not None:
        if not extraction.packet.implementation_slice.strip():
            errors.append("packet is missing an implementation slice")
        if not extraction.packet.out_of_scope:
            errors.append("packet is missing out-of-scope boundaries")
        if not extraction.packet.reflection_prompts:
            errors.append("packet is missing reflection prompts")
    for artifact in [*artifacts, extraction.resident_patch]:
        if not artifact.reason.strip():
            errors.append(f"{artifact.artifact_id} is missing a reason")
    if not extraction.run.provenance_fully_verified:
        errors.append("not all provenance was verified against the source")
    return errors
