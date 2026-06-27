"""Small checks for opt-in real LLM momentum evals."""

from __future__ import annotations

from ravn.momentum.models import MomentumExtraction


def evaluate_extraction(extraction: MomentumExtraction) -> list[str]:
    errors: list[str] = []
    artifacts = extraction.artifacts
    kinds = {artifact.kind for artifact in artifacts}
    for kind in ("durable_insight", "rejected_direction", "unresolved_tension"):
        if kind not in kinds:
            errors.append(f"missing {kind}")
    if not extraction.resident_patch.reason.strip():
        errors.append("resident patch is missing a reason")
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
