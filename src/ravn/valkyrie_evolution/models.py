"""Domain models for Valkyrie self-improvement proof runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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
    #: Hard-gated autonomy boundaries the artifact's reach crosses (e.g.
    #: credentials, spending). The reviewer feeds these to AutonomyPolicy so a
    #: tool that reads secrets is gated by the one policy, not a parallel list.
    risk_boundaries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolReachGrant:
    """Declared external reach a learned tool needs at runtime.

    The manifest is a contract, not a sandbox by itself. Review, install, and
    invocation policy use these grants to decide what may run automatically and
    what must be held for an operator or court decision.
    """

    kind: str
    target: str = ""
    access: str = "read"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | ToolReachGrant) -> ToolReachGrant:
        if isinstance(data, ToolReachGrant):
            return data
        return cls(
            kind=str(data.get("kind") or "pure_compute"),
            target=str(data.get("target") or ""),
            access=str(data.get("access") or "read"),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearnedToolManifest:
    """Agent-callable manifest for a resident-authored instrument."""

    name: str
    description: str
    input_schema: dict[str, Any]
    required_permission: str
    declared_reach: list[ToolReachGrant] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    entry_point: str = "run"
    artifact_type: str = "agent_tool"
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any] | LearnedToolManifest) -> LearnedToolManifest:
        if isinstance(data, LearnedToolManifest):
            return data
        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            input_schema=dict(data.get("input_schema") or {"type": "object"}),
            required_permission=str(data.get("required_permission") or ""),
            declared_reach=[
                ToolReachGrant.from_dict(item) for item in list(data.get("declared_reach") or [])
            ],
            output_schema=dict(data.get("output_schema") or {}),
            entry_point=str(data.get("entry_point") or "run"),
            artifact_type=str(data.get("artifact_type") or "agent_tool"),
            version=int(data.get("version") or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["declared_reach"] = [grant.to_dict() for grant in self.declared_reach]
        return data


@dataclass(frozen=True)
class LearnedToolArtifact:
    """Persistable, flock-shareable resident-authored tool artifact."""

    artifact_id: str
    manifest: LearnedToolManifest
    tool_code: str
    source_signal_ids: list[str] = field(default_factory=list)
    source_session_id: str = ""
    source_gap_id: str = ""
    source_build_id: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def artifact_type(self) -> str:
        return self.manifest.artifact_type

    @classmethod
    def from_dict(cls, data: dict[str, Any] | LearnedToolArtifact) -> LearnedToolArtifact:
        if isinstance(data, LearnedToolArtifact):
            return data
        return cls(
            artifact_id=str(data.get("artifact_id") or ""),
            manifest=LearnedToolManifest.from_dict(dict(data.get("manifest") or {})),
            tool_code=str(data.get("tool_code") or ""),
            source_signal_ids=list(data.get("source_signal_ids") or []),
            source_session_id=str(data.get("source_session_id") or ""),
            source_gap_id=str(data.get("source_gap_id") or ""),
            source_build_id=str(data.get("source_build_id") or ""),
            provenance=dict(data.get("provenance") or {}),
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "manifest": self.manifest.to_dict(),
            "tool_code": self.tool_code,
            "source_signal_ids": list(self.source_signal_ids),
            "source_session_id": self.source_session_id,
            "source_gap_id": self.source_gap_id,
            "source_build_id": self.source_build_id,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }


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
    blocking_findings: list[str] = field(default_factory=list)


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
