"""Typed contracts for the first Momentum Packet pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ArtifactKind = Literal[
    "durable_insight",
    "rejected_direction",
    "unresolved_tension",
    "resident_understanding_patch",
]
ProvenanceStatus = Literal["verified", "unverified"]
AttentionTier = Literal["silent", "ambient", "present", "urgent"]
NextAction = Literal["write_momentum_packet", "update_understanding_only", "ask_human"]


class SourceSpan(BaseModel):
    """Model-selected source context for one extracted artifact."""

    excerpt: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class Provenance(BaseModel):
    signal_kind: Literal["resident_signal"] = "resident_signal"
    source_path: str
    source_sha256: str
    source_excerpt: str
    extraction_run_id: str
    procedure_name: str
    model_name: str
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    line_start: int | None = None
    line_end: int | None = None
    verification_status: ProvenanceStatus
    verification_reason: str


class MomentumArtifactDraft(BaseModel):
    """LLM-facing artifact shape before deterministic provenance is attached."""

    kind: ArtifactKind
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source: SourceSpan
    tags: list[str] = Field(default_factory=list)


class MomentumArtifact(MomentumArtifactDraft):
    artifact_id: str
    provenance: Provenance


class ResidentUnderstandingPatchDraft(BaseModel):
    title: str = Field(default="Resident understanding patch")
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    beliefs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    source: SourceSpan


class ResidentUnderstandingPatch(ResidentUnderstandingPatchDraft):
    artifact_id: str
    kind: Literal["resident_understanding_patch"] = "resident_understanding_patch"
    provenance: Provenance


class MomentumPacketDraft(BaseModel):
    title: str = Field(min_length=1)
    implementation_slice: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    caused_by: list[str] = Field(default_factory=list)
    must_not_lose: list[str] = Field(default_factory=list)
    reuse_guidance: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    success_proof: str = Field(min_length=1)
    reflection_prompts: list[str] = Field(default_factory=list)
    source: SourceSpan

    @field_validator(
        "caused_by",
        "must_not_lose",
        "reuse_guidance",
        "out_of_scope",
        "reflection_prompts",
    )
    @classmethod
    def _must_have_items(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Momentum Packet lists must not be empty")
        return value


class MomentumPacket(MomentumPacketDraft):
    packet_id: str
    provenance: Provenance


class MomentumJudgmentDraft(BaseModel):
    title: str = Field(min_length=1)
    environment_id: str = Field(default="resident:niuu", min_length=1)
    valkyrie_id: str = Field(default="ravn-momentum", min_length=1)
    changed_understanding: str = Field(min_length=1)
    tension_that_matters: str = Field(min_length=1)
    why_attention_now: str = Field(min_length=1)
    recommended_next_action: NextAction
    recommended_action: str = Field(min_length=1)
    attention_tier: AttentionTier = "ambient"
    authority_boundary: str = Field(default="human_review_required", min_length=1)
    operational_state: str = Field(default="proposing", min_length=1)
    confidence: float = Field(ge=0, le=1)
    signal_refs: list[str] = Field(default_factory=list)
    evidence_artifact_titles: list[str] = Field(default_factory=list)
    target_surfaces: list[str] = Field(default_factory=lambda: ["resident/momentum"])
    source: SourceSpan

    @field_validator("evidence_artifact_titles", "signal_refs", "target_surfaces")
    @classmethod
    def _must_have_items(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("judgment lists must not be empty")
        return value


class MomentumJudgment(MomentumJudgmentDraft):
    judgment_id: str
    event_type: Literal["valkyrie.judgment.proposed"] = "valkyrie.judgment.proposed"
    provenance: Provenance


class MomentumExtractionDraft(BaseModel):
    artifacts: list[MomentumArtifactDraft]
    resident_patch: ResidentUnderstandingPatchDraft
    judgment: MomentumJudgmentDraft
    packet: MomentumPacketDraft | None = None


class MomentumExtractionRun(BaseModel):
    run_id: str
    signal_kind: Literal["resident_signal"] = "resident_signal"
    source_path: str
    source_sha256: str
    procedure_name: str
    model_name: str
    created_at: datetime
    provenance_fully_verified: bool
    artifact_refs: list[str]
    judgment_ref: str
    packet_ref: str | None = None


class MomentumExtraction(BaseModel):
    run: MomentumExtractionRun
    artifacts: list[MomentumArtifact]
    resident_patch: ResidentUnderstandingPatch
    judgment: MomentumJudgment
    packet: MomentumPacket | None = None
