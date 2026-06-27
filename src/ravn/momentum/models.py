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


class SourceSpan(BaseModel):
    """Model-selected source context for one extracted artifact."""

    excerpt: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class Provenance(BaseModel):
    source_path: str
    source_sha256: str
    source_excerpt: str
    extraction_run_id: str
    procedure_name: str
    model_name: str
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    line_start: int | None = None
    line_end: int | None = None


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


class MomentumExtractionDraft(BaseModel):
    artifacts: list[MomentumArtifactDraft]
    resident_patch: ResidentUnderstandingPatchDraft
    packet: MomentumPacketDraft


class MomentumExtractionRun(BaseModel):
    run_id: str
    source_path: str
    source_sha256: str
    procedure_name: str
    model_name: str
    created_at: datetime
    artifact_refs: list[str]
    packet_ref: str


class MomentumExtraction(BaseModel):
    run: MomentumExtractionRun
    artifacts: list[MomentumArtifact]
    resident_patch: ResidentUnderstandingPatch
    packet: MomentumPacket
