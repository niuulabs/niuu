"""Small checks for opt-in real LLM momentum evals."""

from __future__ import annotations

from dataclasses import dataclass, field

from ravn.momentum.models import MomentumExtraction
from ravn.momentum.render import judgment_event_payload


@dataclass(frozen=True)
class MomentumDogfoodReport:
    source_ref: str
    mode: str
    model: str
    extraction_procedure: str
    reflection_procedure: str
    run_ref: str
    judgment_ref: str
    packet_ref: str | None
    disposition_ref: str | None
    reflection_ref: str | None
    provenance_verified: bool
    judgment_payload_valid: bool
    packet_judgment_consistent: bool
    reflection_requested: bool
    reflection_produced: bool
    command_hint: str
    config_hint: str
    failures: list[str] = field(default_factory=list)


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


def dogfood_failures(
    extraction: MomentumExtraction,
    *,
    reflection_requested: bool,
    reflection_ref: str | None,
) -> tuple[list[str], bool, bool, bool]:
    errors = evaluate_extraction(extraction)
    judgment_payload_valid = True
    try:
        judgment_event_payload(extraction.judgment)
    except ValueError as exc:
        judgment_payload_valid = False
        errors.append(f"judgment payload invalid: {exc}")

    packet_consistent = _packet_judgment_consistent(extraction)
    if not packet_consistent:
        errors.append("packet/judgment consistency failed")

    reflection_produced = not reflection_requested or bool(reflection_ref)
    if reflection_requested and not reflection_produced:
        errors.append("reflection was requested but not produced")
    return errors, judgment_payload_valid, packet_consistent, reflection_produced


def render_dogfood_report(report: MomentumDogfoodReport) -> str:
    failures = "\n".join(f"- {failure}" for failure in report.failures) or "- none"
    return (
        "# Momentum Dogfood Eval Report\n\n"
        f"- source_ref: {report.source_ref}\n"
        f"- mode: {report.mode}\n"
        f"- model: {report.model}\n"
        f"- extraction_procedure: {report.extraction_procedure}\n"
        f"- reflection_procedure: {report.reflection_procedure or '-'}\n"
        f"- run_ref: {report.run_ref}\n"
        f"- judgment_ref: {report.judgment_ref}\n"
        f"- packet_ref: {report.packet_ref or '-'}\n"
        f"- disposition_ref: {report.disposition_ref or '-'}\n"
        f"- reflection_ref: {report.reflection_ref or '-'}\n"
        f"- provenance_verified: {str(report.provenance_verified).lower()}\n"
        f"- judgment_payload_valid: {str(report.judgment_payload_valid).lower()}\n"
        f"- packet_judgment_consistent: {str(report.packet_judgment_consistent).lower()}\n"
        f"- reflection_requested: {str(report.reflection_requested).lower()}\n"
        f"- reflection_produced: {str(report.reflection_produced).lower()}\n\n"
        "## Eval Failures\n\n"
        f"{failures}\n\n"
        "## Reproduce\n\n"
        f"command: `{report.command_hint}`\n\n"
        "## Adapter Config Hint\n\n"
        f"```text\n{report.config_hint}\n```\n"
    )


def _packet_judgment_consistent(extraction: MomentumExtraction) -> bool:
    action = extraction.judgment.recommended_next_action
    if action == "write_momentum_packet":
        return extraction.packet is not None
    return extraction.packet is None
