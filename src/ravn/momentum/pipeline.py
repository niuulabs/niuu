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
    render_artifact,
    render_disposition,
    render_judgment,
    render_packet,
    render_reflection,
    render_run,
)
from ravn.momentum.source import SourceDocument
from ravn.momentum.worker import MomentumExtractionWorker, MomentumReflectionWorker
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

    @property
    def provenance_fully_verified(self) -> bool:
        return self.extraction.run.provenance_fully_verified


@dataclass(frozen=True)
class MomentumReflectionResult:
    disposition: MomentumJudgmentDisposition
    reflection: MomentumReflection
    disposition_ref: str
    reflection_ref: str


class MomentumPipeline:
    def __init__(
        self,
        *,
        worker: MomentumExtractionWorker,
        reflection_worker: MomentumReflectionWorker | None = None,
        state: ResidentStatePort,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        self._worker = worker
        self._reflection_worker = reflection_worker
        self._state = state
        self._now = now
        self._run_id = run_id

    async def extract_signal(self, signal: ResidentInboxSignal) -> MomentumPipelineResult:
        return await self._extract_text(
            _source_text(signal),
            source_path=signal.raw_ref or signal.id,
        )

    async def _extract_text(
        self,
        markdown: str,
        *,
        source_path: str = "<memory>",
    ) -> MomentumPipelineResult:
        source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        source_doc = SourceDocument(markdown)
        memory = await self._state.recall(
            "Niuu Momentum Engine resident understanding and constraints",
            limit=5,
        )
        draft = await self._worker.extract(
            markdown,
            memory_frame="\n\n".join(entry.content for entry in memory),
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
            }
        )
        run_ref = await self._state.write_artifact(_run_ref(run), render_run(run))
        extraction = extraction.model_copy(update={"run": run})
        return MomentumPipelineResult(
            extraction=extraction,
            run_ref=run_ref,
            artifact_refs=artifact_refs,
            judgment_ref=judgment_ref,
            packet_ref=packet_ref,
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
        draft = await self._reflection_worker.reflect(
            target_ref=disposition.target_ref,
            target_content=target.content,
            run_content=context.run_content,
            judgment_content=context.judgment_content,
            artifact_contents=context.artifact_contents,
            disposition=disposition,
            memory_frame="\n\n".join(entry.content for entry in memory),
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
        return MomentumReflectionResult(
            disposition=disposition,
            reflection=reflection,
            disposition_ref=disposition_ref,
            reflection_ref=reflection_ref,
        )


@dataclass(frozen=True)
class _ReflectionContext:
    run_content: str
    judgment_content: str
    artifact_contents: list[str]


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


async def _read_optional_content(state: ResidentStatePort, ref: str) -> str:
    if not ref or ref == "-":
        return ""
    try:
        return (await state.read_artifact(ref)).content
    except FileNotFoundError:
        return ""


def _parse_field(content: str, field: str) -> str:
    prefix = f"- {field}:"
    for line in content.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


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
