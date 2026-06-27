"""First vertical Momentum Packet pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from ravn.domain.resident_state import ResidentStatePort
from ravn.momentum.models import (
    MomentumArtifact,
    MomentumArtifactDraft,
    MomentumExtraction,
    MomentumExtractionDraft,
    MomentumExtractionRun,
    MomentumJudgment,
    MomentumPacket,
    Provenance,
    ResidentUnderstandingPatch,
)
from ravn.momentum.render import render_artifact, render_judgment, render_packet, render_run
from ravn.momentum.source import SourceDocument
from ravn.momentum.worker import MomentumExtractionWorker
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


class MomentumPipeline:
    def __init__(
        self,
        *,
        worker: MomentumExtractionWorker,
        state: ResidentStatePort,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        self._worker = worker
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
