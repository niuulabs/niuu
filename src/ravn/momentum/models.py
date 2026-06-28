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
AttentionNextAction = Literal[
    "extract_selected_signal",
    "ask_human",
    "update_understanding_only",
    "no_action",
]
DispositionOutcome = Literal["accepted", "dismissed", "wrong", "deferred", "acted"]
MomentumTensionStatus = Literal["pending", "open", "confirmed", "changed", "resolved"]
DelegationTargetKind = Literal[
    "human",
    "codex",
    "ravn",
    "ting",
    "skuld",
    "capability_proposal",
    "none",
]
DelegationProposalKind = Literal[
    "human_question",
    "codex_task",
    "ravn_action_request",
    "ting_workflow_proposal",
    "skuld_huddle",
    "capability_proposal",
    "no_delegation_needed",
]


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
    input_state_ref: str | None = None
    input_state_sha256: str | None = None
    procedure_name: str
    model_name: str
    created_at: datetime
    provenance_fully_verified: bool
    artifact_refs: list[str]
    judgment_ref: str
    packet_ref: str | None = None
    attention_ref: str | None = None
    attention_decision_id: str | None = None
    selected_signal_id: str | None = None
    selected_signal_ref: str | None = None


class MomentumExtraction(BaseModel):
    run: MomentumExtractionRun
    artifacts: list[MomentumArtifact]
    resident_patch: ResidentUnderstandingPatch
    judgment: MomentumJudgment
    packet: MomentumPacket | None = None


class MomentumJudgmentDisposition(BaseModel):
    disposition_id: str
    target_ref: str = Field(min_length=1)
    outcome: DispositionOutcome
    actor: str = Field(min_length=1)
    note: str = Field(min_length=1)
    created_at: datetime
    source: Literal["manual"] = "manual"


class MomentumStateTension(BaseModel):
    tension_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    status: MomentumTensionStatus = "pending"
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MomentumStateTensionPatch(BaseModel):
    tension_id: str = Field(min_length=1)
    title: str | None = None
    summary: str | None = None
    status: MomentumTensionStatus | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class MomentumStatePatchDraft(BaseModel):
    beliefs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    open_tensions: list[MomentumStateTension] = Field(default_factory=list)
    changed_tensions: list[MomentumStateTensionPatch] = Field(default_factory=list)
    resolved_tension_ids: list[str] = Field(default_factory=list)
    confirmed_tension_ids: list[str] = Field(default_factory=list)
    stale_assumptions: list[str] = Field(default_factory=list)
    recent_lessons: list[str] = Field(default_factory=list)
    candidate_reflexes: list[str] = Field(default_factory=list)
    candidate_capability_gaps: list[str] = Field(default_factory=list)

    @field_validator("changed_tensions", mode="before")
    @classmethod
    def _changed_tension_ids_are_patches(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                {"tension_id": item} if isinstance(item, str) else item
                for item in value
            ]
        return value


class MomentumStatePatch(MomentumStatePatchDraft):
    patch_id: str = Field(min_length=1)
    created_at: datetime
    source_refs: list[str] = Field(default_factory=list)


class MomentumResidentState(BaseModel):
    beliefs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    open_tensions: list[MomentumStateTension] = Field(default_factory=list)
    stale_assumptions: list[str] = Field(default_factory=list)
    recent_lessons: list[str] = Field(default_factory=list)
    candidate_reflexes: list[str] = Field(default_factory=list)
    candidate_capability_gaps: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    compaction: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MomentumAttentionDecisionDraft(BaseModel):
    selected_signal_id: str | None = None
    selected_signal_ref: str | None = None
    no_attention_needed: bool = False
    selected_tension_ids: list[str] = Field(default_factory=list)
    attention_tier: AttentionTier = "ambient"
    rationale: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    signal_refs: list[str] = Field(default_factory=list)
    recommended_next_action: AttentionNextAction
    confidence: float = Field(ge=0, le=1)
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("source_refs")
    @classmethod
    def _must_have_source_refs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("attention decision must cite source refs")
        return value


class MomentumAttentionDecision(MomentumAttentionDecisionDraft):
    decision_id: str = Field(min_length=1)
    validation_status: Literal["valid", "invalid"] = "valid"
    created_at: datetime
    current_state_ref: str | None = None
    current_state_present: bool = False
    candidate_count: int = Field(ge=0)
    candidate_limit: int = Field(ge=1)
    candidates_truncated: int = Field(ge=0)
    procedure_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)


class MomentumDelegationTarget(BaseModel):
    target_id: str = Field(min_length=1)
    target_kind: DelegationTargetKind
    display_name: str = Field(min_length=1)
    supported_proposal_kinds: list[DelegationProposalKind]
    authority_boundary: str = Field(min_length=1)
    risk_level: str = Field(default="bounded", min_length=1)
    notes: str = ""

    @field_validator("supported_proposal_kinds")
    @classmethod
    def _must_support_something(
        cls,
        value: list[DelegationProposalKind],
    ) -> list[DelegationProposalKind]:
        if not value:
            raise ValueError("delegation target must support at least one proposal kind")
        return value


class MomentumDelegationProposalDraft(BaseModel):
    selected_target_id: str = Field(min_length=1)
    target_kind: DelegationTargetKind
    proposal_kind: DelegationProposalKind
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    why_this_target: str = Field(min_length=1)
    bounded_request: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    out_of_scope_boundaries: list[str] = Field(default_factory=list)
    authority_boundary: str = Field(min_length=1)
    risk_note: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    execution_allowed_now: bool = False

    @field_validator("execution_allowed_now")
    @classmethod
    def _execution_is_not_allowed(cls, value: bool) -> bool:
        if value:
            raise ValueError("delegation proposal execution is not allowed in v0")
        return value


class MomentumDelegationProposal(MomentumDelegationProposalDraft):
    proposal_id: str = Field(min_length=1)
    source_run_ref: str | None = None
    source_judgment_ref: str = Field(min_length=1)
    source_attention_ref: str | None = None
    source_signal_id: str | None = None
    source_signal_ref: str | None = None
    validation_status: Literal["valid"] = "valid"
    created_at: datetime
    procedure_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)


class MomentumReflectionDraft(BaseModel):
    changed_understanding: str = Field(min_length=1)
    lesson_learned: str = Field(min_length=1)
    original_judgment_useful: bool
    remember_next_time: list[str] = Field(default_factory=list)
    resident_corrections: list[str] = Field(default_factory=list)
    candidate_reflexes: list[str] = Field(default_factory=list)
    candidate_capability_gaps: list[str] = Field(default_factory=list)
    state_patch: MomentumStatePatchDraft = Field(default_factory=MomentumStatePatchDraft)

    @field_validator("remember_next_time")
    @classmethod
    def _must_remember_something(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reflection must include something to remember")
        return value


class MomentumReflection(MomentumReflectionDraft):
    reflection_id: str
    target_ref: str
    disposition_ref: str
    outcome: DispositionOutcome
    actor: str
    procedure_name: str
    model_name: str
    reflected_at: datetime
