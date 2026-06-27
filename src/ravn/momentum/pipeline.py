"""First vertical Momentum Packet pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ravn.domain.resident_state import ResidentStatePort
from ravn.momentum.models import (
    MomentumArtifact,
    MomentumArtifactDraft,
    MomentumExtraction,
    MomentumExtractionDraft,
    MomentumExtractionRun,
    MomentumPacket,
    Provenance,
    ResidentUnderstandingPatch,
)
from ravn.momentum.render import render_artifact, render_packet, render_run
from ravn.momentum.worker import MomentumExtractionWorker
from ravn.resident_continuation import _slug


@dataclass(frozen=True)
class MomentumPipelineResult:
    extraction: MomentumExtraction
    run_ref: str
    artifact_refs: list[str]
    packet_ref: str


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

    async def extract_file(self, path: Path | str) -> MomentumPipelineResult:
        source = Path(path)
        return await self.extract_text(
            source.read_text(encoding="utf-8"),
            source_path=str(source),
        )

    async def extract_text(
        self,
        markdown: str,
        *,
        source_path: str = "<memory>",
    ) -> MomentumPipelineResult:
        source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        memory = await self._state.recall(
            "Niuu Momentum Engine resident understanding and constraints",
            limit=5,
        )
        draft = await self._worker.extract(
            markdown,
            memory_frame="\n\n".join(entry.content for entry in memory),
        )
        created_at = self._now or datetime.now(UTC)
        run_id = self._run_id or f"momentum-{source_sha[:12]}"
        extraction = _materialize(
            draft,
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
        packet_ref = await self._state.write_artifact(
            _packet_ref(extraction.packet),
            render_packet(extraction.packet),
        )
        run = extraction.run.model_copy(
            update={"artifact_refs": artifact_refs, "packet_ref": packet_ref}
        )
        run_ref = await self._state.write_artifact(_run_ref(run), render_run(run))
        extraction = extraction.model_copy(update={"run": run})
        return MomentumPipelineResult(
            extraction=extraction,
            run_ref=run_ref,
            artifact_refs=artifact_refs,
            packet_ref=packet_ref,
        )


def _materialize(
    draft: MomentumExtractionDraft,
    *,
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
    packet_provenance = _provenance(
        draft.packet.source,
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
    run = MomentumExtractionRun(
        run_id=run_id,
        source_path=source_path,
        source_sha256=source_sha256,
        procedure_name=procedure_name,
        model_name=model_name,
        created_at=created_at,
        artifact_refs=[],
        packet_ref="",
    )
    return MomentumExtraction(run=run, artifacts=artifacts, resident_patch=patch, packet=packet)


def _artifact(
    item: MomentumArtifactDraft,
    *,
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
    run_id: str,
    source_path: str,
    source_sha256: str,
    procedure_name: str,
    model_name: str,
    created_at: datetime,
) -> Provenance:
    return Provenance(
        source_path=source_path,
        source_sha256=source_sha256,
        source_excerpt=source.excerpt,
        line_start=source.line_start,
        line_end=source.line_end,
        extraction_run_id=run_id,
        procedure_name=procedure_name,
        model_name=model_name,
        extracted_at=created_at,
    )


def _artifact_ref(artifact: MomentumArtifact | ResidentUnderstandingPatch) -> str:
    return f"resident/momentum/artifacts/{artifact.artifact_id}.md"


def _packet_ref(packet: MomentumPacket) -> str:
    return f"resident/momentum/packets/{packet.packet_id}.md"


def _run_ref(run: MomentumExtractionRun) -> str:
    return f"resident/momentum/runs/{run.run_id}.md"
