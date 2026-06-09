"""Domain models for Valkyrie self-improvement proof runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OperationalSignal:
    signal_id: str
    event_type: str
    environment_id: str
    domain: str
    severity: str
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CapabilityGap:
    gap_id: str
    capability_name: str
    environment_id: str
    domain: str
    reason: str
    signal_ids: list[str]
    evidence: dict[str, Any]
    safety_class: str = "read_only"


@dataclass(frozen=True)
class ValkyrieDecision:
    signal_id: str
    phase: str
    decision: str
    confidence: float
    rationale: str
    capability_name: str
    skill_name: str = ""
    capability_gap: CapabilityGap | None = None


@dataclass(frozen=True)
class EvolutionRequest:
    request_id: str
    gap: CapabilityGap
    autonomy_mode: str
    target_scope: str


@dataclass(frozen=True)
class BuildResult:
    request_id: str
    skill_name: str
    skill_content: str
    description: str
    artifact_type: str
    artifact_path: str = ""
    tool_code: str = ""
    tool_entry_point: str = "run"
    tool_path: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_implementation(self) -> bool:
        return bool(self.tool_code.strip())


@dataclass(frozen=True)
class ReviewResult:
    request_id: str
    artifact_name: str
    approved: bool
    outcome: str
    rationale: str
    reviewer: str
    required_for_activation: bool
    findings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProofArtifacts:
    out_dir: Path
    events_path: Path
    report_json_path: Path
    report_markdown_path: Path
    skills_dir: Path


@dataclass(frozen=True)
class ProofReport:
    summary: dict[str, Any]
    signals: list[dict[str, Any]]
    first_pass_decisions: list[dict[str, Any]]
    dream_cycles: list[dict[str, Any]]
    build_results: list[dict[str, Any]]
    review_results: list[dict[str, Any]]
    replay_decisions: list[dict[str, Any]]
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
