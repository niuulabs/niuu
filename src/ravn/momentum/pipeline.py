"""First vertical Momentum Packet pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ravn.domain.resident_continuation import ResidentMemoryEntry
from ravn.domain.resident_state import ResidentStatePort
from ravn.momentum.models import (
    DispositionOutcome,
    MomentumArtifact,
    MomentumArtifactDraft,
    MomentumAttentionDecision,
    MomentumAttentionDecisionDraft,
    MomentumDelegationProposal,
    MomentumDelegationProposalDraft,
    MomentumDelegationTarget,
    MomentumExtraction,
    MomentumExtractionDraft,
    MomentumExtractionRun,
    MomentumJudgment,
    MomentumJudgmentDisposition,
    MomentumPacket,
    MomentumReflection,
    Provenance,
    ResidentUnderstandingPatch,
)
from ravn.momentum.render import (
    parse_attention_decision,
    render_artifact,
    render_attention_decision,
    render_delegation_proposal,
    render_disposition,
    render_judgment,
    render_packet,
    render_reflection,
    render_run,
)
from ravn.momentum.source import SourceDocument
from ravn.momentum.state import (
    CURRENT_MOMENTUM_STATE_REF,
    BoundedMomentumStateCompactor,
    apply_state_patch,
    empty_momentum_state,
    extraction_state_patch,
    parse_momentum_state,
    reflection_state_patch,
    render_momentum_state,
    render_state_patch,
)
from ravn.momentum.worker import (
    MomentumAttentionWorker,
    MomentumDelegationWorker,
    MomentumExtractionWorker,
    MomentumReflectionWorker,
)
from ravn.ports.momentum_delegation import MomentumDelegationTargetCatalogPort
from ravn.ports.momentum_state_compactor import MomentumStateCompactorPort
from ravn.ports.resident_signal import ResidentSignalSourcePort
from ravn.resident_continuation import _slug
from ravn.resident_inbox.models import ResidentInboxSignal
from ravn.resident_inbox.serialization import render_inbox_signal


@dataclass(frozen=True)
class MomentumPipelineResult:
    extraction: MomentumExtraction
    run_ref: str
    artifact_refs: list[str]
    judgment_ref: str
    packet_ref: str | None
    current_state_ref: str
    state_patch_ref: str

    @property
    def provenance_fully_verified(self) -> bool:
        return self.extraction.run.provenance_fully_verified


@dataclass(frozen=True)
class MomentumReflectionResult:
    disposition: MomentumJudgmentDisposition
    reflection: MomentumReflection
    disposition_ref: str
    reflection_ref: str
    current_state_ref: str
    state_patch_ref: str


@dataclass(frozen=True)
class MomentumAttentionResult:
    decision: MomentumAttentionDecision
    decision_ref: str


@dataclass(frozen=True)
class MomentumDelegationResult:
    proposal: MomentumDelegationProposal
    proposal_ref: str


class MomentumPipeline:
    def __init__(
        self,
        *,
        worker: MomentumExtractionWorker,
        reflection_worker: MomentumReflectionWorker | None = None,
        attention_worker: MomentumAttentionWorker | None = None,
        delegation_worker: MomentumDelegationWorker | None = None,
        state: ResidentStatePort,
        state_compactor: MomentumStateCompactorPort | None = None,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        self._worker = worker
        self._reflection_worker = reflection_worker
        self._attention_worker = attention_worker
        self._delegation_worker = delegation_worker
        self._state = state
        self._state_compactor = state_compactor or BoundedMomentumStateCompactor()
        self._now = now
        self._run_id = run_id

    async def attend(
        self,
        candidates: list[tuple[str, ResidentInboxSignal]],
        *,
        limit: int,
    ) -> MomentumAttentionResult:
        if self._attention_worker is None:
            raise ValueError("Momentum attention worker is required")
        if limit < 1:
            raise ValueError("candidate limit must be at least 1")
        if not candidates:
            raise ValueError("no resident signal candidates found")

        created_at = self._now or datetime.now(UTC)
        bounded = candidates[:limit]
        truncated = max(0, len(candidates) - len(bounded))
        memory = await self._state.recall(
            "Niuu Momentum Engine attention and resident signal selection",
            limit=5,
        )
        current_state, current_state_frame = await _load_current_state(self._state)
        draft = await self._attention_worker.attend(
            memory_frame="\n\n".join(entry.content for entry in memory),
            current_state_frame=current_state_frame,
            candidate_frame=_candidate_frame(bounded),
            truncation_note=_truncation_note(len(candidates), limit, truncated),
        )
        decision = _materialize_attention_decision(
            draft,
            candidates=bounded,
            candidate_count=len(candidates),
            candidate_limit=limit,
            candidates_truncated=truncated,
            current_state_present=current_state is not None,
            created_at=created_at,
            procedure_name=self._attention_worker.procedure_name,
            model_name=self._attention_worker.model,
        )
        ref = await self._state.write_artifact(
            _attention_decision_ref(decision),
            render_attention_decision(decision),
        )
        return MomentumAttentionResult(decision=decision, decision_ref=ref)

    async def extract_signal(self, signal: ResidentInboxSignal) -> MomentumPipelineResult:
        return await self._extract_text(
            _source_text(signal),
            source_path=signal.raw_ref or signal.id,
        )

    async def pursue_attention(
        self,
        attention_ref: str,
        *,
        signal_source: ResidentSignalSourcePort,
    ) -> MomentumPipelineResult:
        try:
            entry = await self._state.read_artifact(attention_ref)
        except FileNotFoundError:
            raise FileNotFoundError(f"attention decision not found: {attention_ref}") from None
        decision = parse_attention_decision(entry.content)
        _validate_pursuable_attention_decision(decision)

        selected = decision.selected_signal_ref or decision.selected_signal_id
        if selected is None:
            raise ValueError("attention decision did not select a signal")

        try:
            signal = await signal_source.load_signal(selected)
        except FileNotFoundError:
            raise ValueError(f"selected signal not found: {selected}") from None
        return await self._extract_text(
            _source_text(signal),
            source_path=signal.raw_ref or signal.id,
            attention_ref=entry.path or attention_ref,
            attention_decision_id=decision.decision_id,
            selected_signal_id=decision.selected_signal_id,
            selected_signal_ref=decision.selected_signal_ref,
        )

    async def _extract_text(
        self,
        markdown: str,
        *,
        source_path: str = "<memory>",
        attention_ref: str | None = None,
        attention_decision_id: str | None = None,
        selected_signal_id: str | None = None,
        selected_signal_ref: str | None = None,
    ) -> MomentumPipelineResult:
        source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        source_doc = SourceDocument(markdown)
        memory = await self._state.recall(
            "Niuu Momentum Engine resident understanding and constraints",
            limit=5,
        )
        current_state, current_state_frame = await _load_current_state(self._state)
        input_state_sha = (
            hashlib.sha256(current_state_frame.encode("utf-8")).hexdigest()
            if current_state_frame
            else None
        )
        draft = await self._worker.extract(
            markdown,
            memory_frame="\n\n".join(entry.content for entry in memory),
            current_state_frame=current_state_frame,
        )
        created_at = self._now or datetime.now(UTC)
        run_id = self._run_id or f"momentum-{created_at:%Y%m%dT%H%M%SZ}-{source_sha[:8]}"
        extraction = _materialize(
            draft,
            source_doc=source_doc,
            run_id=run_id,
            source_path=source_path,
            source_sha256=source_sha,
            procedure_name=self._worker.procedure_name,
            model_name=self._worker.model,
            created_at=created_at,
        )
        artifact_refs = [
            await self._state.write_artifact(_artifact_ref(artifact), render_artifact(artifact))
            for artifact in [*extraction.artifacts, extraction.resident_patch]
        ]
        judgment_ref = await self._state.write_artifact(
            _judgment_ref(extraction.judgment),
            render_judgment(extraction.judgment),
        )
        packet_ref = (
            await self._state.write_artifact(
                _packet_ref(extraction.packet),
                render_packet(extraction.packet),
            )
            if extraction.packet is not None
            else None
        )
        run = extraction.run.model_copy(
            update={
                "artifact_refs": artifact_refs,
                "judgment_ref": judgment_ref,
                "packet_ref": packet_ref,
                "input_state_ref": CURRENT_MOMENTUM_STATE_REF if current_state_frame else None,
                "input_state_sha256": input_state_sha,
                "attention_ref": attention_ref,
                "attention_decision_id": attention_decision_id,
                "selected_signal_id": selected_signal_id,
                "selected_signal_ref": selected_signal_ref,
            }
        )
        run_ref = await self._state.write_artifact(_run_ref(run), render_run(run))
        extraction = extraction.model_copy(update={"run": run})
        state_patch = extraction_state_patch(
            patch_id=f"patch-{_timestamp_id(created_at)}-{run_id}-extract",
            created_at=created_at,
            belief_refs=artifact_refs,
            judgment_title=extraction.judgment.title,
            tension=extraction.judgment.tension_that_matters,
            evidence_refs=[
                ref
                for ref in [*artifact_refs, judgment_ref, packet_ref]
                if ref
            ],
            source_refs=[run_ref, judgment_ref],
            beliefs=extraction.resident_patch.beliefs,
            constraints=extraction.resident_patch.constraints,
            corrections=extraction.resident_patch.corrections,
        )
        current_state = current_state or empty_momentum_state(updated_at=created_at)
        updated_state = self._state_compactor.compact(
            apply_state_patch(current_state, state_patch)
        )
        state_patch_ref = await _write_state_patch(self._state, state_patch)
        current_state_ref = await self._state.write_artifact(
            CURRENT_MOMENTUM_STATE_REF,
            render_momentum_state(updated_state),
        )
        return MomentumPipelineResult(
            extraction=extraction,
            run_ref=run_ref,
            artifact_refs=artifact_refs,
            judgment_ref=judgment_ref,
            packet_ref=packet_ref,
            current_state_ref=current_state_ref,
            state_patch_ref=state_patch_ref,
        )

    async def reflect_judgment(
        self,
        target_ref: str,
        *,
        outcome: DispositionOutcome,
        note: str,
        actor: str = "operator",
    ) -> MomentumReflectionResult:
        if self._reflection_worker is None:
            raise ValueError("Momentum reflection worker is required")

        created_at = self._now or datetime.now(UTC)
        target = await self._state.read_artifact(target_ref)
        context = await _load_reflection_context(self._state, target)
        base_ref = _reflection_base_ref(target.path or target_ref)
        reflection_suffix = f"{_timestamp_id(created_at)}-{outcome}-{uuid4().hex[:6]}"
        disposition = MomentumJudgmentDisposition(
            disposition_id=f"disposition-{reflection_suffix}",
            target_ref=target.path or target_ref,
            outcome=outcome,
            actor=actor,
            note=note,
            created_at=created_at,
        )
        disposition_ref = await self._state.write_artifact(
            f"{base_ref}/dispositions/{disposition.disposition_id}.md",
            render_disposition(disposition),
        )
        memory = await self._state.recall(
            "Niuu Momentum Engine judgment dispositions and reflections",
            limit=5,
        )
        current_state, current_state_frame = await _load_current_state(self._state)
        draft = await self._reflection_worker.reflect(
            target_ref=disposition.target_ref,
            target_content=target.content,
            run_content=context.run_content,
            judgment_content=context.judgment_content,
            artifact_contents=context.artifact_contents,
            disposition=disposition,
            memory_frame="\n\n".join(entry.content for entry in memory),
            current_state_frame=current_state_frame,
        )
        reflection = MomentumReflection(
            **draft.model_dump(),
            reflection_id=f"reflection-{reflection_suffix}",
            target_ref=disposition.target_ref,
            disposition_ref=disposition_ref,
            outcome=outcome,
            actor=actor,
            procedure_name=self._reflection_worker.procedure_name,
            model_name=self._reflection_worker.model,
            reflected_at=created_at,
        )
        reflection_ref = await self._state.write_artifact(
            f"{base_ref}/reflections/{reflection.reflection_id}.md",
            render_reflection(reflection),
        )
        state_patch = reflection_state_patch(
            patch_id=f"patch-{_timestamp_id(created_at)}-{reflection.reflection_id}",
            created_at=created_at,
            source_refs=[disposition.target_ref, disposition_ref, reflection_ref],
            draft=draft.state_patch,
            lesson_learned=reflection.lesson_learned,
            remember_next_time=reflection.remember_next_time,
            corrections=reflection.resident_corrections,
            candidate_reflexes=reflection.candidate_reflexes,
            candidate_capability_gaps=reflection.candidate_capability_gaps,
        )
        current_state = current_state or empty_momentum_state(updated_at=created_at)
        updated_state = self._state_compactor.compact(
            apply_state_patch(current_state, state_patch)
        )
        state_patch_ref = await _write_state_patch(self._state, state_patch)
        current_state_ref = await self._state.write_artifact(
            CURRENT_MOMENTUM_STATE_REF,
            render_momentum_state(updated_state),
        )
        return MomentumReflectionResult(
            disposition=disposition,
            reflection=reflection,
            disposition_ref=disposition_ref,
            reflection_ref=reflection_ref,
            current_state_ref=current_state_ref,
            state_patch_ref=state_patch_ref,
        )

    async def prepare_delegation(
        self,
        source_ref: str,
        *,
        target_catalog: MomentumDelegationTargetCatalogPort,
    ) -> MomentumDelegationResult:
        if self._delegation_worker is None:
            raise ValueError("Momentum delegation worker is required")

        try:
            source = await self._state.read_artifact(source_ref)
        except FileNotFoundError:
            raise FileNotFoundError(f"Momentum judgment or run not found: {source_ref}") from None

        targets = await target_catalog.list_targets()
        if not targets:
            raise ValueError("delegation target catalog is empty")

        context = await _load_delegation_context(self._state, source)
        _, current_state_frame = await _load_current_state(self._state)
        draft = await self._delegation_worker.prepare(
            source_ref=context.source_ref,
            judgment_content=context.judgment_content,
            run_content=context.run_content,
            packet_content=context.packet_content,
            attention_content=context.attention_content,
            artifact_contents=context.artifact_contents,
            current_state_frame=current_state_frame,
            targets=targets,
        )
        created_at = self._now or datetime.now(UTC)
        proposal = _materialize_delegation_proposal(
            draft,
            context=context,
            targets=targets,
            created_at=created_at,
            procedure_name=self._delegation_worker.procedure_name,
            model_name=self._delegation_worker.model,
        )
        proposal_ref = await self._state.write_artifact(
            _delegation_ref(proposal),
            render_delegation_proposal(proposal),
        )
        return MomentumDelegationResult(proposal=proposal, proposal_ref=proposal_ref)


def _materialize_attention_decision(
    draft: MomentumAttentionDecisionDraft,
    *,
    candidates: list[tuple[str, ResidentInboxSignal]],
    candidate_count: int,
    candidate_limit: int,
    candidates_truncated: int,
    current_state_present: bool,
    created_at: datetime,
    procedure_name: str,
    model_name: str,
) -> MomentumAttentionDecision:
    _validate_attention_decision(draft, candidates)
    selected = draft.selected_signal_ref or draft.selected_signal_id or "none"
    return MomentumAttentionDecision(
        **draft.model_dump(),
        decision_id=f"attention-{_timestamp_id(created_at)}-{_slug(selected) or 'none'}",
        created_at=created_at,
        current_state_ref=CURRENT_MOMENTUM_STATE_REF if current_state_present else None,
        current_state_present=current_state_present,
        candidate_count=candidate_count,
        candidate_limit=candidate_limit,
        candidates_truncated=candidates_truncated,
        procedure_name=procedure_name,
        model_name=model_name,
    )


def _validate_attention_decision(
    draft: MomentumAttentionDecisionDraft,
    candidates: list[tuple[str, ResidentInboxSignal]],
) -> None:
    candidate_refs = {ref for ref, _ in candidates}
    candidate_ids = {signal.id for _, signal in candidates}
    candidate_pairs = {(ref, signal.id) for ref, signal in candidates}
    if draft.no_attention_needed:
        if draft.recommended_next_action != "no_action":
            raise ValueError("no_attention_needed requires no_action")
        if draft.selected_signal_id or draft.selected_signal_ref:
            raise ValueError("no_attention_needed cannot select a signal")
        return
    if not draft.selected_signal_id and not draft.selected_signal_ref:
        raise ValueError("attention decision must select a signal or no_attention_needed")
    if draft.selected_signal_id and draft.selected_signal_id not in candidate_ids:
        raise ValueError(f"selected signal id is not a candidate: {draft.selected_signal_id}")
    if draft.selected_signal_ref and draft.selected_signal_ref not in candidate_refs:
        raise ValueError(f"selected signal ref is not a candidate: {draft.selected_signal_ref}")
    if (
        draft.selected_signal_id
        and draft.selected_signal_ref
        and (draft.selected_signal_ref, draft.selected_signal_id) not in candidate_pairs
    ):
        raise ValueError("selected signal id/ref do not refer to the same candidate")


def _validate_pursuable_attention_decision(decision: MomentumAttentionDecision) -> None:
    if decision.validation_status != "valid":
        raise ValueError("attention decision is not valid")
    if decision.no_attention_needed:
        raise ValueError("attention decision says no attention is needed")
    if not decision.selected_signal_id and not decision.selected_signal_ref:
        raise ValueError("attention decision did not select a signal")
    if decision.recommended_next_action != "extract_selected_signal":
        raise ValueError("attention decision does not recommend extracting the selected signal")


def _candidate_frame(candidates: list[tuple[str, ResidentInboxSignal]]) -> str:
    sections: list[str] = []
    for ref, signal in candidates:
        sections.append(
            "### Candidate\n\n"
            f"- ref: {ref}\n"
            f"- id: {signal.id}\n"
            f"- source: {signal.source}\n"
            f"- kind: {signal.kind}\n"
            f"- classification: {signal.classification}\n"
            f"- status: {signal.status}\n"
            f"- summary: {signal.summary}\n"
            f"- evidence_refs: {', '.join(signal.evidence_refs) or '-'}\n"
            f"- raw_ref: {signal.raw_ref or '-'}\n\n"
            "#### Signal Markdown\n\n"
            f"{_source_text(signal)}"
        )
    return "\n\n---\n\n".join(sections)


def _truncation_note(candidate_count: int, limit: int, truncated: int) -> str:
    if not truncated:
        return f"{candidate_count} candidate(s) included; limit {limit}; none truncated."
    return f"{candidate_count} candidate(s) available; limit {limit}; {truncated} truncated."


def _attention_decision_ref(decision: MomentumAttentionDecision) -> str:
    return f"resident/continuation/momentum/attention/{decision.decision_id}.md"


def _delegation_ref(proposal: MomentumDelegationProposal) -> str:
    return f"resident/continuation/momentum/delegations/{proposal.proposal_id}.md"


@dataclass(frozen=True)
class _ReflectionContext:
    run_content: str
    judgment_content: str
    artifact_contents: list[str]


@dataclass(frozen=True)
class _DelegationContext:
    source_ref: str
    run_ref: str | None
    run_content: str
    judgment_ref: str
    judgment_content: str
    packet_ref: str | None
    packet_content: str
    attention_ref: str | None
    attention_content: str
    artifact_refs: list[str]
    artifact_contents: list[str]
    selected_signal_id: str | None
    selected_signal_ref: str | None


async def _load_reflection_context(
    state: ResidentStatePort,
    target: ResidentMemoryEntry,
) -> _ReflectionContext:
    # v0 parser over the rendered run artifact; replace with structured run metadata later.
    target_ref = target.path
    run_content = target.content if target_ref.endswith("/run.md") else ""
    judgment_content = target.content if "/judgment/" in target_ref else ""
    artifact_refs: list[str] = []

    if target_ref.endswith("/run.md"):
        artifact_refs = _parse_artifact_refs(target.content)
        judgment_ref = _parse_field(target.content, "judgment_ref")
        judgment_content = await _read_optional_content(state, judgment_ref)
    elif "/judgment/" in target_ref:
        run_ref = f"{target_ref.split('/judgment/', 1)[0]}/run.md"
        run_content = await _read_optional_content(state, run_ref)
        artifact_refs = _parse_artifact_refs(run_content)

    artifact_contents = [
        content
        for content in [
            await _read_optional_content(state, ref)
            for ref in artifact_refs
        ]
        if content
    ]
    return _ReflectionContext(
        run_content=run_content,
        judgment_content=judgment_content,
        artifact_contents=artifact_contents,
    )


async def _load_delegation_context(
    state: ResidentStatePort,
    source: ResidentMemoryEntry,
) -> _DelegationContext:
    source_ref = source.path
    if source_ref.endswith("/run.md"):
        return await _delegation_context_from_run(state, source_ref, source.content)
    if "/judgment/" in source_ref:
        return await _delegation_context_from_judgment(state, source_ref, source.content)
    raise ValueError("Momentum delegation source must be a run or judgment artifact")


async def _delegation_context_from_run(
    state: ResidentStatePort,
    run_ref: str,
    run_content: str,
) -> _DelegationContext:
    judgment_ref = _parse_field(run_content, "judgment_ref")
    if not judgment_ref:
        raise ValueError("Momentum run is missing judgment_ref")
    judgment_content = await _read_required_content(
        state,
        judgment_ref,
        f"Momentum run judgment not found: {judgment_ref}",
    )
    return await _delegation_context(
        state,
        source_ref=run_ref,
        run_ref=run_ref,
        run_content=run_content,
        judgment_ref=judgment_ref,
        judgment_content=judgment_content,
    )


async def _delegation_context_from_judgment(
    state: ResidentStatePort,
    judgment_ref: str,
    judgment_content: str,
) -> _DelegationContext:
    run_ref = f"{judgment_ref.split('/judgment/', 1)[0]}/run.md"
    run_content = await _read_optional_content(state, run_ref)
    return await _delegation_context(
        state,
        source_ref=judgment_ref,
        run_ref=run_ref if run_content else None,
        run_content=run_content,
        judgment_ref=judgment_ref,
        judgment_content=judgment_content,
    )


async def _delegation_context(
    state: ResidentStatePort,
    *,
    source_ref: str,
    run_ref: str | None,
    run_content: str,
    judgment_ref: str,
    judgment_content: str,
) -> _DelegationContext:
    packet_ref = _optional_run_field(run_content, "packet_ref")
    attention_ref = _optional_run_field(run_content, "attention_ref")
    artifact_refs = _parse_artifact_refs(run_content)
    artifact_contents = [
        content
        for content in [
            await _read_optional_content(state, ref)
            for ref in artifact_refs
        ]
        if content
    ]
    return _DelegationContext(
        source_ref=source_ref,
        run_ref=run_ref,
        run_content=run_content,
        judgment_ref=judgment_ref,
        judgment_content=judgment_content,
        packet_ref=packet_ref,
        packet_content=await _read_optional_content(state, packet_ref or ""),
        attention_ref=attention_ref,
        attention_content=await _read_optional_content(state, attention_ref or ""),
        artifact_refs=artifact_refs,
        artifact_contents=artifact_contents,
        selected_signal_id=_optional_run_field(run_content, "selected_signal_id"),
        selected_signal_ref=_optional_run_field(run_content, "selected_signal_ref"),
    )


async def _read_required_content(
    state: ResidentStatePort,
    ref: str,
    error: str,
) -> str:
    try:
        return (await state.read_artifact(ref)).content
    except FileNotFoundError:
        raise FileNotFoundError(error) from None


async def _read_optional_content(state: ResidentStatePort, ref: str) -> str:
    if not ref or ref == "-":
        return ""
    try:
        return (await state.read_artifact(ref)).content
    except FileNotFoundError:
        return ""


def _materialize_delegation_proposal(
    draft: MomentumDelegationProposalDraft,
    *,
    context: _DelegationContext,
    targets: list[MomentumDelegationTarget],
    created_at: datetime,
    procedure_name: str,
    model_name: str,
) -> MomentumDelegationProposal:
    _validate_delegation_proposal(draft, targets)
    proposal_id = (
        f"delegation-{_timestamp_id(created_at)}-"
        f"{_slug(draft.selected_target_id)}-{_slug(draft.proposal_kind)}"
    )
    return MomentumDelegationProposal(
        **draft.model_dump(),
        proposal_id=proposal_id,
        source_run_ref=context.run_ref,
        source_judgment_ref=context.judgment_ref,
        source_attention_ref=context.attention_ref,
        source_signal_id=context.selected_signal_id,
        source_signal_ref=context.selected_signal_ref,
        created_at=created_at,
        procedure_name=procedure_name,
        model_name=model_name,
    )


def _validate_delegation_proposal(
    draft: MomentumDelegationProposalDraft,
    targets: list[MomentumDelegationTarget],
) -> None:
    target = next(
        (item for item in targets if item.target_id == draft.selected_target_id),
        None,
    )
    if target is None:
        raise ValueError(f"delegation target is not available: {draft.selected_target_id}")
    if draft.target_kind != target.target_kind:
        raise ValueError(
            f"delegation target kind mismatch for {target.target_id}: "
            f"{draft.target_kind} != {target.target_kind}"
        )
    if draft.proposal_kind not in target.supported_proposal_kinds:
        raise ValueError(
            f"delegation target {target.target_id} does not support "
            f"{draft.proposal_kind}"
        )
    if draft.execution_allowed_now:
        raise ValueError("delegation proposal execution is not allowed in v0")


async def _load_current_state(state: ResidentStatePort):
    try:
        entry = await state.read_artifact(CURRENT_MOMENTUM_STATE_REF)
    except FileNotFoundError:
        return None, ""
    return parse_momentum_state(entry.content), entry.content


async def _write_state_patch(state: ResidentStatePort, patch) -> str:
    return await state.write_artifact(
        f"resident/continuation/momentum/state/patches/{patch.patch_id}.md",
        render_state_patch(patch),
    )


def _parse_field(content: str, field: str) -> str:
    prefix = f"- {field}:"
    for line in content.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _optional_run_field(content: str, field: str) -> str | None:
    value = _parse_field(content, field)
    if not value or value == "-":
        return None
    return value


def _parse_artifact_refs(content: str) -> list[str]:
    if "## Artifact Refs" not in content:
        return []
    _, tail = content.split("## Artifact Refs", 1)
    refs: list[str] = []
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            refs.append(stripped.removeprefix("- ").strip())
    return refs


def _reflection_base_ref(target_ref: str) -> str:
    if "/runs/" in target_ref:
        run_id = target_ref.split("/runs/", 1)[1].split("/", 1)[0]
        return f"resident/continuation/momentum/runs/{run_id}"
    slug = _slug(target_ref) or "momentum-judgment"
    return f"resident/continuation/momentum/{slug}"


def _timestamp_id(created_at: datetime) -> str:
    return created_at.strftime("%Y%m%dT%H%M%SZ")


def _materialize(
    draft: MomentumExtractionDraft,
    *,
    source_doc: SourceDocument,
    run_id: str,
    source_path: str,
    source_sha256: str,
    procedure_name: str,
    model_name: str,
    created_at: datetime,
) -> MomentumExtraction:
    _validate_draft(draft)
    artifacts = [
        _artifact(
            item,
            source_doc=source_doc,
            index=index,
            run_id=run_id,
            source_path=source_path,
            source_sha256=source_sha256,
            procedure_name=procedure_name,
            model_name=model_name,
            created_at=created_at,
        )
        for index, item in enumerate(draft.artifacts, start=1)
    ]
    patch_provenance = _provenance(
        draft.resident_patch.source,
        source_doc=source_doc,
        run_id=run_id,
        source_path=source_path,
        source_sha256=source_sha256,
        procedure_name=procedure_name,
        model_name=model_name,
        created_at=created_at,
    )
    patch = ResidentUnderstandingPatch(
        **draft.resident_patch.model_dump(),
        artifact_id=f"resident-understanding-{run_id}",
        provenance=patch_provenance,
    )
    judgment_provenance = _provenance(
        draft.judgment.source,
        source_doc=source_doc,
        run_id=run_id,
        source_path=source_path,
        source_sha256=source_sha256,
        procedure_name=procedure_name,
        model_name=model_name,
        created_at=created_at,
    )
    judgment = MomentumJudgment(
        **draft.judgment.model_dump(),
        judgment_id=f"judgment-{_slug(draft.judgment.title) or run_id}",
        provenance=judgment_provenance,
    )
    packet = None
    if draft.packet is not None:
        packet_provenance = _provenance(
            draft.packet.source,
            source_doc=source_doc,
            run_id=run_id,
            source_path=source_path,
            source_sha256=source_sha256,
            procedure_name=procedure_name,
            model_name=model_name,
            created_at=created_at,
        )
        packet = MomentumPacket(
            **draft.packet.model_dump(),
            packet_id=f"packet-{_slug(draft.packet.title) or run_id}",
            provenance=packet_provenance,
        )
    provenance_fully_verified = all(
        item.provenance.verification_status == "verified"
        for item in [*artifacts, patch, judgment, *([packet] if packet is not None else [])]
    )
    run = MomentumExtractionRun(
        run_id=run_id,
        source_path=source_path,
        source_sha256=source_sha256,
        procedure_name=procedure_name,
        model_name=model_name,
        created_at=created_at,
        provenance_fully_verified=provenance_fully_verified,
        artifact_refs=[],
        judgment_ref="",
        packet_ref=None,
    )
    return MomentumExtraction(
        run=run,
        artifacts=artifacts,
        resident_patch=patch,
        judgment=judgment,
        packet=packet,
    )


def _validate_draft(draft: MomentumExtractionDraft) -> None:
    evidence_titles = {artifact.title for artifact in draft.artifacts} | {
        draft.resident_patch.title
    }
    missing = [
        title
        for title in draft.judgment.evidence_artifact_titles
        if title not in evidence_titles
    ]
    if missing:
        raise ValueError(f"judgment evidence titles not found: {', '.join(missing)}")

    next_action = draft.judgment.recommended_next_action
    if next_action == "write_momentum_packet" and draft.packet is None:
        raise ValueError("judgment requires a Momentum Packet")
    if next_action != "write_momentum_packet" and draft.packet is not None:
        raise ValueError("judgment does not require a Momentum Packet")


def _artifact(
    item: MomentumArtifactDraft,
    *,
    source_doc: SourceDocument,
    index: int,
    run_id: str,
    source_path: str,
    source_sha256: str,
    procedure_name: str,
    model_name: str,
    created_at: datetime,
) -> MomentumArtifact:
    return MomentumArtifact(
        **item.model_dump(),
        artifact_id=f"{item.kind}-{index}-{_slug(item.title) or 'artifact'}",
        provenance=_provenance(
            item.source,
            source_doc=source_doc,
            run_id=run_id,
            source_path=source_path,
            source_sha256=source_sha256,
            procedure_name=procedure_name,
            model_name=model_name,
            created_at=created_at,
        ),
    )


def _provenance(
    source,
    *,
    source_doc: SourceDocument,
    run_id: str,
    source_path: str,
    source_sha256: str,
    procedure_name: str,
    model_name: str,
    created_at: datetime,
) -> Provenance:
    verification = source_doc.verify(source)
    return Provenance(
        source_path=source_path,
        source_sha256=source_sha256,
        source_excerpt=verification.excerpt,
        line_start=verification.line_start,
        line_end=verification.line_end,
        extraction_run_id=run_id,
        procedure_name=procedure_name,
        model_name=model_name,
        extracted_at=created_at,
        verification_status=verification.status,
        verification_reason=verification.reason,
    )


def _artifact_ref(artifact: MomentumArtifact | ResidentUnderstandingPatch) -> str:
    run_id = artifact.provenance.extraction_run_id
    return f"resident/momentum/runs/{run_id}/artifacts/{artifact.artifact_id}.md"


def _packet_ref(packet: MomentumPacket) -> str:
    run_id = packet.provenance.extraction_run_id
    return f"resident/momentum/runs/{run_id}/packet/{packet.packet_id}.md"


def _judgment_ref(judgment: MomentumJudgment) -> str:
    run_id = judgment.provenance.extraction_run_id
    return f"resident/momentum/runs/{run_id}/judgment/{judgment.judgment_id}.md"


def _run_ref(run: MomentumExtractionRun) -> str:
    return f"resident/momentum/runs/{run.run_id}/run.md"


def _source_text(signal: ResidentInboxSignal) -> str:
    content = signal.payload.get("content")
    if isinstance(content, str) and content.strip():
        return f"{render_inbox_signal(signal)}\n## Payload Content\n\n{content}"
    return render_inbox_signal(signal)
