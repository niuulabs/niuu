from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_signal import (
    MarkdownResidentSignalSource,
    MimirResidentInboxSignalSource,
)
from ravn.adapters.resident_state.gbrain import GBrainResidentStateAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState, MimirResidentState
from ravn.cli import commands
from ravn.domain.models import LLMResponse, StopReason, TokenUsage
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_JUDGMENT_PROPOSED,
    validate_valkyrie_outcome,
)
from ravn.momentum import (
    MomentumAttentionWorker,
    MomentumExtractionWorker,
    MomentumPipeline,
    MomentumReflectionWorker,
)
from ravn.momentum.models import MomentumStatePatch, MomentumStateTension
from ravn.momentum.render import judgment_event_payload
from ravn.momentum.state import (
    CURRENT_MOMENTUM_STATE_REF,
    MAX_BELIEFS,
    MAX_CANDIDATE_CAPABILITY_GAPS,
    MAX_CANDIDATE_REFLEXES,
    MAX_OPEN_TENSIONS,
    BoundedMomentumStateCompactor,
    apply_state_patch,
    empty_momentum_state,
    render_momentum_state,
)
from ravn.ports.resident_signal import ResidentSignalSourcePort
from ravn.resident_inbox import (
    MimirResidentInbox,
    ResidentInboxClassification,
    ResidentInboxSignal,
)


class FakeLLM:
    def __init__(self, payload: dict | list[dict]) -> None:
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict] = []

    async def generate(self, messages, *, tools, system, model, max_tokens, thinking=None):
        payload = self.payloads[min(len(self.calls), len(self.payloads) - 1)]
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            content=json.dumps(payload),
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=20),
        )

    def stream(self, *args, **kwargs):
        raise NotImplementedError


class RecordingGBrainState(GBrainResidentStateAdapter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.put_pages: list[tuple[str, str, str]] = []

    async def recall(self, mandate: str, *, limit: int = 5) -> list:
        return []

    async def _put_page_gbrain(self, ref, title, content) -> None:
        self.put_pages.append((ref, title, content))


class RecordingStateCompactor:
    def __init__(self) -> None:
        self.calls = 0

    def compact(self, state):
        self.calls += 1
        return state.model_copy(
            update={"beliefs": [*state.beliefs, "compactor touched state"]}
        )


class StaticCandidateSource:
    def __init__(self, candidates: list[tuple[str, ResidentInboxSignal]]) -> None:
        self.candidates = candidates
        self.calls: list[dict] = []

    async def list_candidates(
        self,
        *,
        limit: int,
        status: str = "",
        classification: str = "",
    ) -> list[tuple[str, ResidentInboxSignal]]:
        self.calls.append(
            {"limit": limit, "status": status, "classification": classification}
        )
        items = [
            item
            for item in self.candidates
            if (not status or item[1].status == status)
            and (not classification or item[1].classification == classification)
        ]
        return items[:limit]


async def _markdown_signal(path: Path) -> ResidentInboxSignal:
    return await MarkdownResidentSignalSource().load_signal(str(path))


def _candidate(signal_id: str, summary: str, content: str) -> ResidentInboxSignal:
    return ResidentInboxSignal(
        id=signal_id,
        source="test:resident",
        kind="operator.directed_message",
        summary=summary,
        payload={"content": content},
        raw_ref=f"resident/inbox/signals/{signal_id}.md",
        classification=ResidentInboxClassification.IDEA.value,
        evidence_refs=(f"evidence:{signal_id}",),
        created_at=datetime(2026, 6, 27, 12, tzinfo=UTC),
    )


def _attention_payload(
    *,
    selected_id: str | None = "sig-relevant",
    selected_ref: str | None = "resident/inbox/signals/sig-relevant.md",
    no_attention_needed: bool = False,
) -> dict:
    return {
        "selected_signal_id": selected_id,
        "selected_signal_ref": selected_ref,
        "no_attention_needed": no_attention_needed,
        "selected_tension_ids": (
            [] if no_attention_needed else ["tension-carry-reflected-state"]
        ),
        "attention_tier": "present",
        "rationale": (
            "The selected signal directly answers the current open tension."
            if not no_attention_needed
            else "No candidate matches the current resident state strongly enough."
        ),
        "why_now": (
            "The current state names this tension as open and the signal supplies evidence."
            if not no_attention_needed
            else "The candidates do not change current Momentum understanding."
        ),
        "evidence_refs": ["resident/continuation/momentum/state/current.md"],
        "signal_refs": [selected_ref or selected_id or "none"],
        "recommended_next_action": (
            "no_action" if no_attention_needed else "extract_selected_signal"
        ),
        "confidence": 0.82,
        "source_refs": ["resident/continuation/momentum/state/current.md"],
    }


@pytest.mark.asyncio
async def test_markdown_signal_source_loads_manual_resident_signal(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")

    signal = await MarkdownResidentSignalSource().load_signal(str(source))

    assert signal.kind == "manual.markdown"
    assert signal.raw_ref == str(source)
    assert signal.payload["content"] == "# Notes\n\nImportant living idea."
    assert signal.classification == ResidentInboxClassification.SOURCE_EVIDENCE.value


@pytest.mark.asyncio
async def test_mimir_resident_inbox_signal_source_loads_signal_by_id_and_ref(tmp_path: Path):
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    signal = ResidentInboxSignal(
        id="sig-source",
        source="skuld:telegram",
        kind="operator.directed_message",
        summary="Operator notes about momentum dilution",
        payload={"content": "# Notes\n\nImportant living idea."},
        classification=ResidentInboxClassification.IDEA.value,
    )
    ref = await inbox.write_signal(signal)
    source = MimirResidentInboxSignalSource(mimir)

    by_id = await source.load_signal("sig-source")
    by_ref = await source.load_signal(ref)

    assert by_id.id == "sig-source"
    assert by_id.raw_ref == ref
    assert by_ref.id == "sig-source"
    assert by_ref.raw_ref == ref


@pytest.mark.asyncio
async def test_mimir_resident_inbox_signal_source_lists_attention_candidates(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    idea = _candidate("sig-idea", "Momentum idea", "Idea content.")
    risk = _candidate("sig-risk", "Momentum risk", "Risk content.").with_updates(
        classification=ResidentInboxClassification.RISK.value
    )
    await inbox.write_signal(idea)
    await inbox.write_signal(risk)

    candidates = await MimirResidentInboxSignalSource(mimir).list_candidates(
        limit=5,
        status="new",
        classification=ResidentInboxClassification.RISK.value,
    )

    assert [(ref.endswith("-sig-risk.md"), signal.id) for ref, signal in candidates] == [
        (True, "sig-risk")
    ]
    assert candidates[0][0].endswith("-sig-risk.md")


@pytest.mark.asyncio
async def test_pipeline_only_needs_signal_source_port_and_normalized_signal(tmp_path: Path):
    class StaticSignalSource:
        async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal:
            return ResidentInboxSignal(
                id=ref_or_id,
                source="test:anywhere",
                kind="future.signal",
                summary="Future source signal",
                payload={"content": "Important living idea."},
                raw_ref=f"future:{ref_or_id}",
                classification=ResidentInboxClassification.UNKNOWN.value,
            )

    source: ResidentSignalSourcePort = StaticSignalSource()
    llm = FakeLLM(_payload())

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="source-port",
    ).extract_signal(await source.load_signal("sig-anywhere"))

    frame = llm.calls[0]["messages"][0]["content"]
    assert "source: test:anywhere" in frame
    assert "kind: future.signal" in frame
    assert result.run_ref == "resident/momentum/runs/source-port/run.md"


def test_momentum_package_does_not_import_concrete_signal_storage() -> None:
    forbidden = (
        "ravn.adapters.",
        "ravn.cli",
        "MimirResidentInbox",
        "GBrainResidentStateAdapter",
        "mimir.adapters",
    )

    for path in Path("src/ravn/momentum").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(item in text for item in forbidden), path


@pytest.mark.asyncio
async def test_momentum_pipeline_persists_typed_artifacts_with_selected_state(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(_payload())
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="run-test",
    )

    result = await pipeline.extract_signal(await _markdown_signal(source))

    frame = llm.calls[0]["messages"][0]["content"]
    assert "Resident Inbox Signal" in frame
    assert "kind: manual.markdown" in frame
    assert "# Messy notes" in frame
    assert (
        result.packet_ref
        == "resident/momentum/runs/run-test/packet/packet-build-momentum-pipeline.md"
    )
    assert len(result.artifact_refs) == 4
    assert result.provenance_fully_verified is True
    refs = await state.list_refs("resident/momentum")
    assert result.run_ref in refs
    assert result.judgment_ref in refs
    assert result.packet_ref in refs
    packet = (tmp_path / "state" / result.packet_ref).read_text(encoding="utf-8")
    run = (tmp_path / "state" / result.run_ref).read_text(encoding="utf-8")
    assert "- signal_kind: resident_signal" in run
    assert "## Why It Matters" in packet
    assert "## Out Of Scope" in packet
    assert "Mimir" in packet
    artifact = (tmp_path / "state" / result.artifact_refs[0]).read_text(encoding="utf-8")
    assert "- source_path:" in artifact
    assert "- extraction_run_id: run-test" in artifact
    assert "- provenance_status: verified" in artifact
    assert "The model said this mattered." in artifact
    judgment = (tmp_path / "state" / result.judgment_ref).read_text(encoding="utf-8")
    assert "- event_type: valkyrie.judgment.proposed" in judgment
    assert "## Tension That Matters" in judgment
    assert "Signal compression is diluting resident understanding." in judgment
    current_state = (tmp_path / "state" / CURRENT_MOMENTUM_STATE_REF).read_text(
        encoding="utf-8"
    )
    state_patch = (tmp_path / "state" / result.state_patch_ref).read_text(encoding="utf-8")
    assert "Signal compression is diluting resident understanding." in current_state
    assert "- status: pending" in current_state
    assert result.judgment_ref in state_patch


@pytest.mark.asyncio
async def test_pipeline_processes_conversation_resident_inbox_signal(tmp_path: Path):
    llm = FakeLLM(_payload())
    signal = ResidentInboxSignal(
        id="sig-conversation",
        source="skuld:telegram",
        kind="operator.directed_message",
        summary="Operator notes about momentum dilution",
        payload={"content": "# Notes\n\nImportant living idea."},
        classification=ResidentInboxClassification.IDEA.value,
        status="new",
        evidence_refs=("telegram:42",),
    )

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="conversation-signal",
    ).extract_signal(signal)

    frame = llm.calls[0]["messages"][0]["content"]
    assert "- id: sig-conversation" in frame
    assert "classification: idea" in frame
    assert "telegram:42" in frame
    assert result.judgment_ref


@pytest.mark.asyncio
async def test_pipeline_processes_non_conversation_resident_inbox_signal(tmp_path: Path):
    llm = FakeLLM(_payload())
    signal = ResidentInboxSignal(
        id="sig-printer-risk",
        source="octoprint",
        kind="environment.signal",
        summary="Printer enclosure temperature is rising",
        payload={"severity": "warning", "content": "Important living idea."},
        classification=ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
        status="new",
        evidence_refs=("sensor:enclosure-temp",),
    )

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="physical-signal",
    ).extract_signal(signal)

    frame = llm.calls[0]["messages"][0]["content"]
    assert "kind: environment.signal" in frame
    assert "classification: physical_observation" in frame
    assert "sensor:enclosure-temp" in frame
    assert result.extraction.judgment.event_type == "valkyrie.judgment.proposed"


@pytest.mark.asyncio
async def test_judgment_payload_validates_against_valkyrie_contract(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["judgment"]["attention_tier"] = "silent"

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="canonical-judgment",
    ).extract_signal(await _markdown_signal(source))

    judgment_payload = judgment_event_payload(result.extraction.judgment)
    assert judgment_payload["tier"] == "silent"
    assert validate_valkyrie_outcome(VALKYRIE_JUDGMENT_PROPOSED, judgment_payload) == []


@pytest.mark.asyncio
async def test_current_momentum_state_is_absent_on_first_run_then_persisted(
    tmp_path: Path,
):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(_payload())

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="state-first",
    ).extract_signal(await _markdown_signal(source))

    frame = llm.calls[0]["messages"][0]["content"]
    assert "## Current Momentum state\n\n(none)" in frame
    current = await state.read_artifact(result.current_state_ref)
    run = await state.read_artifact(result.run_ref)
    assert result.current_state_ref == CURRENT_MOMENTUM_STATE_REF
    assert "- input_state_ref: -" in run.content
    assert "- input_state_sha256: -" in run.content
    assert "## Open Tensions" in current.content
    assert "Signal compression is diluting resident understanding." in current.content
    assert "tension-attend-to-momentum-dilution" in current.content


@pytest.mark.asyncio
async def test_current_momentum_state_is_included_on_later_runs(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")

    await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="state-before",
    ).extract_signal(await _markdown_signal(source))
    next_llm = FakeLLM(_payload())

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(next_llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, 1, tzinfo=UTC),
        run_id="state-after",
    ).extract_signal(await _markdown_signal(source))

    frame = next_llm.calls[0]["messages"][0]["content"]
    run = await state.read_artifact(result.run_ref)
    assert "## Current Momentum state" in frame
    assert "# Current Momentum Resident State" in frame
    assert "Signal compression is diluting resident understanding." in frame
    assert f"- input_state_ref: {CURRENT_MOMENTUM_STATE_REF}" in run.content
    assert "- input_state_sha256: -" not in run.content


def test_current_momentum_state_compacts_old_entries() -> None:
    created_at = datetime(2026, 6, 27, 12, tzinfo=UTC)
    patch = MomentumStatePatch(
        patch_id="patch-compact",
        created_at=created_at,
        beliefs=[f"belief {index}" for index in range(MAX_BELIEFS + 2)],
        candidate_reflexes=[
            f"Candidate: reflex {index}" for index in range(MAX_CANDIDATE_REFLEXES + 1)
        ],
        candidate_capability_gaps=[
            f"Candidate: gap {index}" for index in range(MAX_CANDIDATE_CAPABILITY_GAPS + 1)
        ],
        open_tensions=[
            MomentumStateTension(
                tension_id=f"tension-{index}",
                title=f"Tension {index}",
                summary=f"Summary {index}",
                updated_at=datetime(2026, 6, 27, 12, index, tzinfo=UTC),
            )
            for index in range(MAX_OPEN_TENSIONS + 1)
        ],
    )

    state = BoundedMomentumStateCompactor().compact(
        apply_state_patch(empty_momentum_state(updated_at=created_at), patch)
    )
    rendered = render_momentum_state(state)

    assert state.beliefs == [f"belief {index}" for index in range(2, MAX_BELIEFS + 2)]
    assert [item.tension_id for item in state.open_tensions] == [
        f"tension-{index}" for index in range(1, MAX_OPEN_TENSIONS + 1)
    ]
    assert "belief 0" not in rendered
    assert "tension-0" not in rendered
    assert "Candidate Reflexes (candidate-only)" in rendered
    assert "Candidate Capability Gaps (candidate-only)" in rendered
    assert "Candidate: reflex 0" not in rendered
    assert "Candidate: gap 0" not in rendered
    assert "## State Compaction" in rendered
    assert "- beliefs_truncated: 2 older entries omitted" in rendered
    assert "- open_tensions_truncated: 1 older entry omitted" in rendered
    assert "- candidate_reflexes_truncated: 1 older entry omitted" in rendered
    assert "- candidate_capability_gaps_truncated: 1 older entry omitted" in rendered


@pytest.mark.asyncio
async def test_momentum_pipeline_uses_injected_state_compactor(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    compactor = RecordingStateCompactor()

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        state_compactor=compactor,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="inject-compactor",
    ).extract_signal(await _markdown_signal(source))

    current = await state.read_artifact(result.current_state_ref)
    assert compactor.calls == 1
    assert "compactor touched state" in current.content


@pytest.mark.asyncio
async def test_momentum_attention_reads_current_state_and_persists_decision(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    current = empty_momentum_state(updated_at=datetime(2026, 6, 27, 12, tzinfo=UTC))
    current = current.model_copy(
        update={
            "open_tensions": [
                MomentumStateTension(
                    tension_id="tension-carry-reflected-state",
                    title="Carry reflected state",
                    summary="Future attention should prefer signals that resolve reflected state.",
                    status="open",
                )
            ]
        }
    )
    await state.write_artifact(CURRENT_MOMENTUM_STATE_REF, render_momentum_state(current))
    candidates = [
        (
            "resident/inbox/signals/sig-distractor.md",
            _candidate("sig-distractor", "Unrelated shell preference", "Use fish later."),
        ),
        (
            "resident/inbox/signals/sig-relevant.md",
            _candidate(
                "sig-relevant",
                "Evidence about reflected state handoff",
                "The reflected Momentum state is visible in the next signal.",
            ),
        ),
    ]
    llm = FakeLLM(_attention_payload())

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        attention_worker=MomentumAttentionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 13, tzinfo=UTC),
    ).attend(candidates, limit=5)

    assert result.decision_ref.startswith("resident/continuation/momentum/attention/")
    assert result.decision.selected_signal_id == "sig-relevant"
    assert result.decision.current_state_present is True
    assert "Carry reflected state" in llm.calls[0]["messages"][0]["content"]
    assert "sig-distractor" in llm.calls[0]["messages"][0]["content"]
    assert "sig-relevant" in llm.calls[0]["messages"][0]["content"]
    artifact = await state.read_artifact(result.decision_ref)
    assert "## Rationale" in artifact.content
    assert "selected_signal_id: sig-relevant" in artifact.content
    assert "validation_status: valid" in artifact.content
    assert "current_state_ref: resident/continuation/momentum/state/current.md" in artifact.content


@pytest.mark.asyncio
async def test_momentum_attention_without_current_state_records_absence(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(
        _attention_payload(
            selected_id=None,
            selected_ref=None,
            no_attention_needed=True,
        )
    )

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        attention_worker=MomentumAttentionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 13, tzinfo=UTC),
    ).attend(
        [
            (
                "resident/inbox/signals/sig-quiet.md",
                _candidate("sig-quiet", "Routine status", "Nothing changed."),
            )
        ],
        limit=5,
    )

    assert result.decision.no_attention_needed is True
    assert result.decision.current_state_present is False
    assert "## Current Momentum state\n\n(none)" in llm.calls[0]["messages"][0]["content"]
    artifact = await state.read_artifact(result.decision_ref)
    assert "current_state_present: false" in artifact.content
    assert "no_attention_needed: true" in artifact.content


@pytest.mark.asyncio
async def test_momentum_attention_valid_no_attention_decision_persists(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(
        _attention_payload(
            selected_id=None,
            selected_ref=None,
            no_attention_needed=True,
        )
    )

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        attention_worker=MomentumAttentionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 13, tzinfo=UTC),
    ).attend(
        [
            (
                "resident/inbox/signals/sig-quiet.md",
                _candidate("sig-quiet", "Routine status", "Nothing changed."),
            )
        ],
        limit=5,
    )

    artifact = await state.read_artifact(result.decision_ref)
    assert result.decision.no_attention_needed is True
    assert result.decision.recommended_next_action == "no_action"
    assert "recommended_next_action: no_action" in artifact.content


@pytest.mark.asyncio
async def test_momentum_attention_rejects_unknown_selected_signal(tmp_path: Path) -> None:
    state = LocalResidentState(tmp_path / "state")
    payload = _attention_payload(
        selected_id="sig-missing",
        selected_ref="resident/inbox/signals/sig-missing.md",
    )

    with pytest.raises(ValueError, match="selected signal id is not a candidate"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            attention_worker=MomentumAttentionWorker(FakeLLM(payload), model="fake-model"),
            state=state,
        ).attend(
            [
                (
                    "resident/inbox/signals/sig-known.md",
                    _candidate("sig-known", "Known signal", "Known content."),
                )
            ],
            limit=5,
        )


@pytest.mark.asyncio
async def test_momentum_attention_rejects_mismatched_selected_id_ref(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    payload = _attention_payload(
        selected_id="sig-one",
        selected_ref="resident/inbox/signals/sig-two.md",
    )

    with pytest.raises(
        ValueError,
        match="selected signal id/ref do not refer to the same candidate",
    ):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            attention_worker=MomentumAttentionWorker(FakeLLM(payload), model="fake-model"),
            state=state,
        ).attend(
            [
                (
                    "resident/inbox/signals/sig-one.md",
                    _candidate("sig-one", "First signal", "First content."),
                ),
                (
                    "resident/inbox/signals/sig-two.md",
                    _candidate("sig-two", "Second signal", "Second content."),
                ),
            ],
            limit=5,
        )


@pytest.mark.asyncio
async def test_momentum_attention_rejects_no_attention_with_extract_action(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    payload = _attention_payload(
        selected_id=None,
        selected_ref=None,
        no_attention_needed=True,
    )
    payload["recommended_next_action"] = "extract_selected_signal"

    with pytest.raises(ValueError, match="no_attention_needed requires no_action"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            attention_worker=MomentumAttentionWorker(FakeLLM(payload), model="fake-model"),
            state=state,
        ).attend(
            [
                (
                    "resident/inbox/signals/sig-quiet.md",
                    _candidate("sig-quiet", "Routine status", "Nothing changed."),
                )
            ],
            limit=5,
        )


@pytest.mark.asyncio
async def test_momentum_attention_records_candidate_truncation(tmp_path: Path) -> None:
    state = LocalResidentState(tmp_path / "state")
    candidates = [
        (
            f"resident/inbox/signals/sig-{index}.md",
            _candidate(f"sig-{index}", f"Signal {index}", f"Content {index}"),
        )
        for index in range(3)
    ]
    llm = FakeLLM(
        _attention_payload(
            selected_id="sig-1",
            selected_ref="resident/inbox/signals/sig-1.md",
        )
    )

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        attention_worker=MomentumAttentionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 13, tzinfo=UTC),
    ).attend(candidates, limit=2)

    frame = llm.calls[0]["messages"][0]["content"]
    assert "3 candidate(s) available; limit 2; 1 truncated." in frame
    assert "sig-2" not in frame
    assert result.decision.candidates_truncated == 1
    artifact = await state.read_artifact(result.decision_ref)
    assert "candidates_truncated: 1" in artifact.content


@pytest.mark.asyncio
async def test_momentum_attention_valid_selected_signal_decision_persists(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        attention_worker=MomentumAttentionWorker(
            FakeLLM(
                _attention_payload(
                    selected_id="sig-relevant",
                    selected_ref="resident/inbox/signals/sig-relevant.md",
                )
            ),
            model="fake-model",
        ),
        state=state,
        now=datetime(2026, 6, 27, 13, tzinfo=UTC),
    ).attend(
        [
            (
                "resident/inbox/signals/sig-relevant.md",
                _candidate("sig-relevant", "Relevant signal", "Relevant content."),
            )
        ],
        limit=5,
    )

    artifact = await state.read_artifact(result.decision_ref)
    assert result.decision.selected_signal_id == "sig-relevant"
    assert result.decision.selected_signal_ref == "resident/inbox/signals/sig-relevant.md"
    assert "selected_signal_id: sig-relevant" in artifact.content


@pytest.mark.asyncio
async def test_judgment_rejects_old_watch_attention_tier(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["judgment"]["attention_tier"] = "watch"

    with pytest.raises(ValueError, match="attention_tier"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
            state=LocalResidentState(tmp_path / "state"),
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="bad-tier",
        ).extract_signal(await _markdown_signal(source))


@pytest.mark.asyncio
async def test_judgment_rejects_missing_evidence_artifact_title(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["judgment"]["evidence_artifact_titles"] = ["Missing artifact"]

    with pytest.raises(ValueError, match="Missing artifact"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
            state=LocalResidentState(tmp_path / "state"),
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="bad-evidence",
        ).extract_signal(await _markdown_signal(source))


@pytest.mark.asyncio
async def test_judgment_can_update_understanding_without_packet(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["judgment"]["recommended_next_action"] = "update_understanding_only"
    payload["packet"] = None

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="no-packet",
    ).extract_signal(await _markdown_signal(source))

    assert result.packet_ref is None
    assert result.extraction.packet is None
    assert result.extraction.judgment.recommended_next_action == "update_understanding_only"
    run = (tmp_path / "state" / result.run_ref).read_text(encoding="utf-8")
    assert "- judgment_ref: resident/momentum/runs/no-packet/judgment/" in run
    assert "- packet_ref: -" in run


@pytest.mark.asyncio
async def test_packet_required_when_judgment_recommends_packet(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["packet"] = None

    with pytest.raises(ValueError, match="requires a Momentum Packet"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
            state=LocalResidentState(tmp_path / "state"),
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="missing-packet",
        ).extract_signal(await _markdown_signal(source))


@pytest.mark.asyncio
async def test_packet_rejected_when_judgment_does_not_recommend_packet(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["judgment"]["recommended_next_action"] = "ask_human"

    with pytest.raises(ValueError, match="does not require a Momentum Packet"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
            state=LocalResidentState(tmp_path / "state"),
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="extra-packet",
        ).extract_signal(await _markdown_signal(source))


@pytest.mark.asyncio
async def test_pipeline_writes_through_mimir_resident_state(tmp_path: Path):
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    state = MimirResidentState(mimir)
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="mimir-signal",
    ).extract_signal(await _markdown_signal(source))

    assert "valkyrie.judgment.proposed" in await mimir.read_page(result.judgment_ref)
    assert "Resident Signal Momentum Run" in await mimir.read_page(result.run_ref)
    assert result.packet_ref is not None
    assert "## Implementation Slice" in await mimir.read_page(result.packet_ref)
    assert result.artifact_refs
    assert "## Why It Matters" in await mimir.read_page(result.artifact_refs[0])


@pytest.mark.asyncio
async def test_pipeline_writes_through_gbrain_resident_state(tmp_path: Path):
    state = RecordingGBrainState(
        tmp_path / "gbrain",
        write_mode="put_page",
        mcp_url="https://brain.example/mcp",
        api_token="tok",
    )
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nImportant living idea.", encoding="utf-8")

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="gbrain-signal",
    ).extract_signal(await _markdown_signal(source))

    refs = [ref for ref, _title, _content in state.put_pages]
    assert result.run_ref in refs
    assert result.judgment_ref in refs
    assert result.packet_ref in refs
    assert set(result.artifact_refs).issubset(refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["accepted", "dismissed", "wrong"])
async def test_judgment_disposition_produces_persisted_reflection(
    tmp_path: Path,
    outcome: str,
):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM([_payload(), _reflection_payload(outcome)])
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id=f"reflect-{outcome}",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))
    original_judgment = await state.read_artifact(extraction.judgment_ref)

    result = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome=outcome,
        note=f"Operator marked it {outcome}.",
    )

    assert (await state.read_artifact(extraction.judgment_ref)).content == original_judgment.content
    assert result.disposition.outcome == outcome
    assert result.reflection.outcome == outcome
    disposition = await state.read_artifact(result.disposition_ref)
    reflection = await state.read_artifact(result.reflection_ref)
    current_state = await state.read_artifact(result.current_state_ref)
    state_patch = await state.read_artifact(result.state_patch_ref)
    assert f"- outcome: {outcome}" in disposition.content
    assert f"Lesson for {outcome}" in reflection.content
    assert f"Lesson for {outcome}" in current_state.content
    assert result.disposition_ref in state_patch.content
    assert result.reflection_ref in state_patch.content
    assert "Original Judgment Useful" in reflection.content


@pytest.mark.asyncio
async def test_repeated_reflections_same_second_get_distinct_refs(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM([_payload(), _reflection_payload("accepted")])
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="same-second",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    first = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="First acceptance.",
    )
    second = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="Second acceptance.",
    )

    assert first.disposition_ref != second.disposition_ref
    assert first.reflection_ref != second.reflection_ref
    assert "First acceptance." in (await state.read_artifact(first.disposition_ref)).content
    assert "Second acceptance." in (await state.read_artifact(second.disposition_ref)).content


@pytest.mark.asyncio
async def test_wrong_disposition_persists_model_authored_correction(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    correction = "Correction from reflection model only."
    llm = FakeLLM([_payload(), _reflection_payload("wrong", correction=correction)])
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="wrong-correction",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    result = await pipeline.reflect_judgment(
        extraction.run_ref,
        outcome="wrong",
        note="The judgment misread the operator intent.",
    )

    reflection = await state.read_artifact(result.reflection_ref)
    current_state = await state.read_artifact(result.current_state_ref)
    assert correction in reflection.content
    assert correction in current_state.content
    assert correction not in llm.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_reflection_proceeds_when_related_run_context_is_missing(tmp_path: Path):
    state = LocalResidentState(tmp_path / "state")
    run_ref = await state.write_artifact(
        "resident/momentum/runs/missing-context/run.md",
        "# Resident Signal Momentum Run missing-context\n\n"
        "- run_id: missing-context\n\n"
        "## Summary\n\n"
        "No rendered judgment_ref or artifact refs yet.\n",
    )
    llm = FakeLLM(_reflection_payload("deferred"))

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
    ).reflect_judgment(
        run_ref,
        outcome="deferred",
        note="Need more evidence.",
    )

    prompt = llm.calls[0]["messages"][0]["content"]
    assert result.reflection_ref
    assert "## Judgment artifact\n\n(unavailable)" in prompt
    assert "## Related artifacts\n\n(none)" in prompt


@pytest.mark.asyncio
async def test_reflection_ignores_missing_optional_related_artifacts(tmp_path: Path):
    state = LocalResidentState(tmp_path / "state")
    run_ref = await state.write_artifact(
        "resident/momentum/runs/missing-related/run.md",
        "# Resident Signal Momentum Run missing-related\n\n"
        "- run_id: missing-related\n"
        "- judgment_ref: resident/momentum/runs/missing-related/judgment/missing.md\n\n"
        "## Artifact Refs\n\n"
        "- resident/momentum/runs/missing-related/artifacts/missing.md\n",
    )
    llm = FakeLLM(_reflection_payload("acted"))

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
    ).reflect_judgment(
        run_ref,
        outcome="acted",
        note="Action completed.",
    )

    prompt = llm.calls[0]["messages"][0]["content"]
    assert result.reflection_ref
    assert "## Judgment artifact\n\n(unavailable)" in prompt
    assert "## Related artifacts\n\n(none)" in prompt


@pytest.mark.asyncio
async def test_reflex_and_capability_gap_outputs_remain_candidates(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM([_payload(), _reflection_payload("accepted")])
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="candidate-only",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    result = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )

    reflection = await state.read_artifact(result.reflection_ref)
    current_state = await state.read_artifact(result.current_state_ref)
    refs = await state.list_refs("resident/continuation/momentum")
    assert "- candidate_reflex_status: candidate_only" in reflection.content
    assert "- candidate_capability_gap_status: candidate_only" in reflection.content
    assert "Candidate Reflexes" in reflection.content
    assert "Candidate Capability Gaps" in reflection.content
    assert "Candidate Reflexes (candidate-only)" in current_state.content
    assert "Candidate Capability Gaps (candidate-only)" in current_state.content
    assert "Candidate: ask for disposition after action." in current_state.content
    assert "Candidate: no automatic outcome feed." in current_state.content
    assert all("/reflex" not in ref and "/capabilit" not in ref for ref in refs)


@pytest.mark.asyncio
async def test_reflection_state_patch_can_confirm_tension(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(
        [
            _payload(),
            _reflection_payload(
                "accepted",
                state_patch={
                    "confirmed_tension_ids": ["tension-attend-to-momentum-dilution"],
                    "recent_lessons": ["Confirmed state patch lesson."],
                },
            ),
        ]
    )
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="confirm-tension",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    result = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )

    current_state = await state.read_artifact(result.current_state_ref)
    reflection_frame = llm.calls[1]["messages"][0]["content"]
    assert "## Current Momentum state" in reflection_frame
    assert "Signal compression is diluting resident understanding." in reflection_frame
    assert "- status: confirmed" in current_state.content
    assert "Confirmed state patch lesson." in current_state.content


@pytest.mark.asyncio
async def test_reflection_state_patch_can_change_existing_tension_with_partial_update(
    tmp_path: Path,
):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(
        [
            _payload(),
            _reflection_payload(
                "accepted",
                state_patch={
                    "changed_tensions": [
                        {
                            "tension_id": "tension-attend-to-momentum-dilution",
                            "status": "changed",
                        }
                    ],
                },
            ),
        ]
    )
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="partial-change",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    result = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )

    current_state = await state.read_artifact(result.current_state_ref)
    assert "- status: changed" in current_state.content
    assert "Signal compression is diluting resident understanding." in current_state.content


@pytest.mark.asyncio
async def test_reflection_state_patch_can_change_tension_with_id_shorthand(
    tmp_path: Path,
):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM(
        [
            _payload(),
            _reflection_payload(
                "accepted",
                state_patch={
                    "changed_tensions": ["tension-attend-to-momentum-dilution"],
                },
            ),
        ]
    )
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="id-change",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))

    result = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )

    current_state = await state.read_artifact(result.current_state_ref)
    assert "- status: changed" in current_state.content
    assert "Signal compression is diluting resident understanding." in current_state.content


@pytest.mark.asyncio
async def test_state_updates_do_not_mutate_historical_artifacts(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    llm = FakeLLM([_payload(), _reflection_payload("accepted"), _payload()])
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="immutable-history",
    )
    extraction = await pipeline.extract_signal(await _markdown_signal(source))
    reflection = await pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )
    judgment_before = (await state.read_artifact(extraction.judgment_ref)).content
    disposition_before = (await state.read_artifact(reflection.disposition_ref)).content
    reflection_before = (await state.read_artifact(reflection.reflection_ref)).content

    await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, 1, tzinfo=UTC),
        run_id="immutable-history-next",
    ).extract_signal(await _markdown_signal(source))

    assert (await state.read_artifact(extraction.judgment_ref)).content == judgment_before
    assert (await state.read_artifact(reflection.disposition_ref)).content == disposition_before
    assert (await state.read_artifact(reflection.reflection_ref)).content == reflection_before


@pytest.mark.asyncio
async def test_future_momentum_extraction_recalls_reflected_learning(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    first_llm = FakeLLM([_payload(), _reflection_payload("accepted")])
    first_pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(first_llm, model="fake-model"),
        reflection_worker=MomentumReflectionWorker(first_llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="learned-once",
    )
    extraction = await first_pipeline.extract_signal(await _markdown_signal(source))
    await first_pipeline.reflect_judgment(
        extraction.judgment_ref,
        outcome="accepted",
        note="The judgment helped.",
    )
    next_llm = FakeLLM(_payload())

    await MomentumPipeline(
        worker=MomentumExtractionWorker(next_llm, model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, 1, tzinfo=UTC),
        run_id="recall-reflection",
    ).extract_signal(await _markdown_signal(source))

    assert "Lesson for accepted" in next_llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_momentum_pipeline_replays_same_refs_with_same_fake_output(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("same input", encoding="utf-8")
    now = datetime(2026, 6, 27, 12, tzinfo=UTC)

    first = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "one"),
        now=now,
        run_id="run-replay",
    ).extract_signal(await _markdown_signal(source))
    second = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "two"),
        now=now,
        run_id="run-replay",
    ).extract_signal(await _markdown_signal(source))

    assert first.packet_ref == second.packet_ref
    assert first.judgment_ref == second.judgment_ref
    assert first.artifact_refs == second.artifact_refs
    assert first.extraction.model_dump(mode="json") == second.extraction.model_dump(mode="json")


@pytest.mark.asyncio
async def test_default_runs_are_new_extractions_not_silent_overwrites(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")

    first = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
    ).extract_signal(await _markdown_signal(source))
    second = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, 1, tzinfo=UTC),
    ).extract_signal(await _markdown_signal(source))

    assert first.run_ref != second.run_ref
    assert first.packet_ref != second.packet_ref
    assert first.judgment_ref != second.judgment_ref
    assert first.extraction.run.run_id.startswith("momentum-20260627T120000Z-")
    assert second.extraction.run.run_id.startswith("momentum-20260627T120100Z-")


@pytest.mark.asyncio
async def test_ungrounded_model_output_is_persisted_as_unverified(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    payload = _payload()
    payload["artifacts"][0]["source"] = {"excerpt": "not present in the source"}

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(payload), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="ungrounded",
    ).extract_signal(await _markdown_signal(source))

    assert result.provenance_fully_verified is False
    artifact = (tmp_path / "state" / result.artifact_refs[0]).read_text(encoding="utf-8")
    run = (tmp_path / "state" / result.run_ref).read_text(encoding="utf-8")
    assert "- provenance_status: unverified" in artifact
    assert "- provenance_fully_verified: false" in run
    assert result.judgment_ref.endswith("/judgment/judgment-attend-to-momentum-dilution.md")


@pytest.mark.asyncio
async def test_recorded_model_response_from_noisy_fixture_is_grounded(tmp_path: Path):
    fixture = Path("tests/test_ravn/fixtures/niuu_vision_noisy_transcript.md")
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_vision_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="vision-proof",
    ).extract_signal(await _markdown_signal(fixture))

    artifacts = [*result.extraction.artifacts, result.extraction.resident_patch]
    kinds = {artifact.kind for artifact in result.extraction.artifacts}
    assert {"durable_insight", "rejected_direction", "unresolved_tension"} <= kinds
    assert len(result.extraction.artifacts) >= 10
    assert all(artifact.reason for artifact in artifacts)
    assert all(
        artifact.provenance.source_path.endswith("niuu_vision_noisy_transcript.md")
        for artifact in artifacts
    )
    assert result.extraction.packet.out_of_scope
    assert result.extraction.packet.reuse_guidance
    assert result.extraction.packet.reflection_prompts
    assert result.extraction.judgment.changed_understanding
    assert result.extraction.judgment.tension_that_matters
    assert result.extraction.judgment.recommended_next_action == "write_momentum_packet"
    assert result.provenance_fully_verified is True


def test_momentum_extract_cli_runs_pipeline(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")

    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    state = LocalResidentState(tmp_path / "state")

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "extract", str(source)])

    assert result.exit_code == 0, result.output
    assert "run_id:" in result.output
    assert "artifacts:   4" in result.output
    assert "judgment_ref:resident/momentum/runs/" in result.output
    assert (
        "packet_ref:  resident/momentum/runs/"
        in result.output
    )
    assert "provenance:  verified" in result.output
    assert f"current_state_ref: {CURRENT_MOMENTUM_STATE_REF}" in result.output
    assert "state_patch_ref:   resident/continuation/momentum/state/patches/" in result.output
    current_state = asyncio.run(state.read_artifact(CURRENT_MOMENTUM_STATE_REF))
    assert "Signal compression is diluting resident understanding." in current_state.content


def test_momentum_inbox_cli_runs_pipeline(monkeypatch, tmp_path: Path):
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    signal = ResidentInboxSignal(
        id="sig-cli",
        source="skuld:telegram",
        kind="operator.directed_message",
        summary="Operator notes about momentum dilution",
        payload={"content": "# Notes\n\nImportant living idea."},
        classification=ResidentInboxClassification.IDEA.value,
        evidence_refs=("telegram:99",),
    )

    import anyio

    anyio.run(inbox.write_signal, signal)
    monkeypatch.setattr(commands, "_build_mimir", lambda _settings: mimir)
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return LocalResidentState(tmp_path / "state")

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "inbox", "sig-cli"])

    assert result.exit_code == 0, result.output
    assert "judgment_ref:resident/momentum/runs/" in result.output
    assert "packet_ref:  resident/momentum/runs/" in result.output
    assert f"current_state_ref: {CURRENT_MOMENTUM_STATE_REF}" in result.output


def test_momentum_attend_cli_prints_decision_without_executing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    candidate_ref = "resident/inbox/signals/sig-relevant.md"
    source = StaticCandidateSource(
        [
            (
                candidate_ref,
                _candidate(
                    "sig-relevant",
                    "Evidence about reflected state handoff",
                    "The reflected state is visible.",
                ),
            )
        ]
    )
    monkeypatch.setattr(commands, "_build_resident_inbox_signal_source", lambda _s: source)
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_attention_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        ["momentum", "attend", "--limit", "1", "--status", "new"],
    )

    assert result.exit_code == 0, result.output
    assert "attention_ref: resident/continuation/momentum/attention/" in result.output
    assert "selected_signal_id: sig-relevant" in result.output
    assert f"selected_signal_ref: {candidate_ref}" in result.output
    assert "attention_tier: present" in result.output
    assert "recommended_next_action: extract_selected_signal" in result.output
    assert "confidence: 0.82" in result.output
    assert "run_ref:" not in result.output
    assert source.calls == [{"limit": 2, "status": "new", "classification": ""}]
    assert asyncio.run(state.list_refs("resident/momentum/runs")) == []


def test_momentum_attend_cli_empty_candidates_fails(monkeypatch, tmp_path: Path) -> None:
    source = StaticCandidateSource([])
    monkeypatch.setattr(commands, "_build_resident_inbox_signal_source", lambda _s: source)

    result = CliRunner().invoke(commands.app, ["momentum", "attend", "--limit", "1"])

    assert result.exit_code == 1
    assert "No resident signal candidates found." in result.output


def test_momentum_reflect_cli_records_disposition_and_reflection(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        result = await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            state=state,
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="cli-reflect",
        ).extract_signal(await _markdown_signal(source))
        return result.judgment_ref

    target_ref = asyncio.run(_seed())
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_reflection_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        [
            "momentum",
            "reflect",
            target_ref,
            "--outcome",
            "accepted",
            "--note",
            "Operator accepted it.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "disposition_ref: resident/continuation/momentum/runs/cli-reflect/" in result.output
    assert "reflection_ref:  resident/continuation/momentum/runs/cli-reflect/" in result.output
    assert f"current_state_ref: {CURRENT_MOMENTUM_STATE_REF}" in result.output
    assert "state_patch_ref:   resident/continuation/momentum/state/patches/" in result.output
    current_state = asyncio.run(state.read_artifact(CURRENT_MOMENTUM_STATE_REF))
    assert "Lesson for accepted" in current_state.content


def test_later_momentum_extract_cli_includes_current_state(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    first_llm = FakeLLM([_payload(), _reflection_payload("accepted")])

    async def _seed() -> None:
        pipeline = MomentumPipeline(
            worker=MomentumExtractionWorker(first_llm, model="fake-model"),
            reflection_worker=MomentumReflectionWorker(first_llm, model="fake-model"),
            state=state,
            now=datetime(2026, 6, 27, 12, tzinfo=UTC),
            run_id="cli-current-state",
        )
        extraction = await pipeline.extract_signal(await _markdown_signal(source))
        await pipeline.reflect_judgment(
            extraction.judgment_ref,
            outcome="accepted",
            note="The judgment helped.",
        )

    asyncio.run(_seed())
    next_llm = FakeLLM(_payload())
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: next_llm)

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "extract", str(source)])

    assert result.exit_code == 0, result.output
    frame = next_llm.calls[0]["messages"][0]["content"]
    assert "## Current Momentum state" in frame
    assert "Lesson for accepted" in frame


def test_momentum_reflect_cli_rejects_invalid_outcome(monkeypatch, tmp_path: Path):
    async def _state(_settings, _workspace):
        return LocalResidentState(tmp_path / "state")

    monkeypatch.setattr(commands, "_build_resident_state", _state)
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_reflection_payload()))

    result = CliRunner().invoke(
        commands.app,
        [
            "momentum",
            "reflect",
            "resident/momentum/runs/demo/run.md",
            "--outcome",
            "maybe",
            "--note",
            "Operator was unclear.",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid outcome: maybe" in result.output
    assert "accepted, dismissed, wrong, deferred, acted" in result.output


def test_momentum_extract_uses_configured_command_llm(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    command = tmp_path / "local_llm.py"
    command.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        f"print({json.dumps(_payload())!r})\n",
        encoding="utf-8",
    )
    config = tmp_path / "ravn.yaml"
    config.write_text(
        "llm:\n"
        "  model: command-test\n"
        "  provider:\n"
        "    adapter: ravn.adapters.llm.command.CommandLLMAdapter\n"
        "    kwargs:\n"
        f"      command: {sys.executable}\n"
        "      args:\n"
        f"        - {command}\n",
        encoding="utf-8",
    )
    state = LocalResidentState(tmp_path / "state")

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        ["momentum", "extract", str(source), "--config", str(config)],
    )
    os.environ.pop("RAVN_CONFIG", None)

    assert result.exit_code == 0, result.output
    assert "provenance:  verified" in result.output
    assert "judgment_ref:resident/momentum/runs/" in result.output


def _payload() -> dict:
    return {
        "artifacts": [
            _artifact("durable_insight", "Niuu as a Momentum Engine"),
            _artifact("rejected_direction", "Do not hardwire Mimir"),
            _artifact("unresolved_tension", "Autonomy proposal needs reset"),
        ],
        "resident_patch": {
            "title": "Resident understanding patch",
            "summary": "Preserve momentum ideas through selected resident state.",
            "reason": "The resident needs durable context before execution.",
            "beliefs": ["Momentum Packets preserve why work matters."],
            "constraints": ["Use selected resident state, not a fixed backend."],
            "corrections": ["Do not build fake autonomy."],
            "source": {"excerpt": "Important living idea."},
        },
        "judgment": {
            "title": "Attend to momentum dilution",
            "environment_id": "resident:niuu",
            "valkyrie_id": "ravn-momentum",
            "changed_understanding": (
                "The signal shows resident understanding needs to preserve why work matters."
            ),
            "tension_that_matters": "Signal compression is diluting resident understanding.",
            "why_attention_now": (
                "The resident has enough grounded evidence to protect the idea before execution."
            ),
            "recommended_next_action": "write_momentum_packet",
            "recommended_action": "Write one bounded Momentum Packet for the safe slice.",
            "attention_tier": "ambient",
            "authority_boundary": "human_review_required",
            "operational_state": "proposing",
            "confidence": 0.82,
            "signal_refs": ["resident-signal:test"],
            "evidence_artifact_titles": [
                "Niuu as a Momentum Engine",
                "Autonomy proposal needs reset",
            ],
            "target_surfaces": ["resident/momentum"],
            "source": {"excerpt": "Important living idea."},
        },
        "packet": {
            "title": "Build Momentum pipeline",
            "implementation_slice": "Add a small typed extraction and persistence path.",
            "why_it_matters": "It prevents a living idea from flattening into a normal ticket.",
            "caused_by": ["Niuu as a Momentum Engine"],
            "must_not_lose": ["Reasons and rejected directions travel with the packet."],
            "reuse_guidance": ["Reuse Ravn and selected resident state."],
            "out_of_scope": ["No scheduler", "No UI", "No Mimir hardwire"],
            "success_proof": "A fake model can replay the same artifacts and packet refs.",
            "reflection_prompts": ["What changed in resident understanding?"],
            "source": {"excerpt": "Important living idea."},
        },
    }


def _reflection_payload(
    outcome: str = "accepted",
    *,
    correction: str = "Remember to compare the judgment with the outcome.",
    state_patch: dict | None = None,
) -> dict:
    payload = {
        "changed_understanding": f"The {outcome} outcome updates resident memory.",
        "lesson_learned": f"Lesson for {outcome}",
        "original_judgment_useful": outcome != "wrong",
        "remember_next_time": [f"Check disposition outcomes like {outcome}."],
        "resident_corrections": [correction] if outcome == "wrong" else [],
        "candidate_reflexes": ["Candidate: ask for disposition after action."],
        "candidate_capability_gaps": ["Candidate: no automatic outcome feed."],
    }
    if state_patch is not None:
        payload["state_patch"] = state_patch
    return payload


def _vision_payload() -> dict:
    titles = [
        (
            "durable_insight",
            "Niuu as a Momentum Engine",
            "Niuu is a Momentum Engine because humans create intent faster "
            "than they can execute it",
        ),
        (
            "durable_insight",
            "Drive preserves momentum",
            "Drive is closer to unresolved tension inside the resident understanding.",
        ),
        (
            "durable_insight",
            "Work does not begin with a prompt",
            "Work does not begin with a prompt;",
        ),
        (
            "durable_insight",
            "Goals are prompting in disguise",
            "goals are prompting in disguise.",
        ),
        (
            "durable_insight",
            "Learned reflexes over hardcoded rules",
            "Learned reflexes can become deterministic later, after experience, "
            "review, and promotion.",
        ),
        (
            "durable_insight",
            "Self-awareness as operational self-modeling",
            "Only in the operational self-modeling sense.",
        ),
        (
            "durable_insight",
            "Protect insight from context dilution",
            'nearly flattened it into "build an agent workflow."',
        ),
        (
            "durable_insight",
            "Use the selected memory backend",
            "Use the selected resident memory backend",
        ),
        (
            "durable_insight",
            "LLM council semantic authority",
            "semantic authority should be LLM or council driven.",
        ),
        (
            "unresolved_tension",
            "Autonomy proposal needs reset",
            "Useful intent, messy code. It probably needs reset",
        ),
        (
            "durable_insight",
            "Bounded cognitive workers for context hygiene",
            "Use bounded cognitive workers or subagents",
        ),
        (
            "rejected_direction",
            "Responsibility rejected as product language",
            "Responsibility sounds like guardrails and external product language.",
        ),
        (
            "rejected_direction",
            "Avoid mobile-hostile vision cadence",
            "mobile-hostile one-line paragraph/list cadence",
        ),
    ]
    payload = _payload()
    payload["artifacts"] = [
        _artifact(kind, title, excerpt, line_start=None, line_end=None)
        for kind, title, excerpt in titles
    ]
    payload["packet"]["title"] = "Preserve Niuu vision before execution"
    payload["packet"]["implementation_slice"] = (
        "Implement the first typed Momentum Packet extraction proof only."
    )
    payload["resident_patch"]["source"] = {
        "excerpt": "The first target is Niuu itself.",
    }
    payload["judgment"] = {
        "title": "Attend to context dilution tension",
        "changed_understanding": (
            "The signal shows Niuu should treat noisy resident input as a source "
            "of understanding, not just a document to summarize."
        ),
        "tension_that_matters": (
            "Live vision is being flattened into generic agent workflow work."
        ),
        "why_attention_now": (
            "The source explicitly says the first target is Niuu itself and the "
            "failure is flattening a thought into agent workflow."
        ),
        "recommended_next_action": "write_momentum_packet",
        "recommended_action": (
            "Persist the judgment and write one bounded Momentum Packet for the "
            "first safe implementation slice."
        ),
        "attention_tier": "ambient",
        "authority_boundary": "human_review_required",
        "operational_state": "proposing",
        "confidence": 0.84,
        "signal_refs": ["tests/test_ravn/fixtures/niuu_vision_noisy_transcript.md"],
        "evidence_artifact_titles": [
            "Protect insight from context dilution",
            "Autonomy proposal needs reset",
        ],
        "target_surfaces": ["resident/momentum"],
        "source": {
            "excerpt": 'nearly flattened it into "build an agent workflow."',
        },
    }
    payload["packet"]["source"] = {
        "excerpt": "No scheduler, no daemon, no UI, no workflow runtime, no fake autonomy.",
    }
    return payload


def _artifact(
    kind: str,
    title: str,
    excerpt: str | None = None,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict:
    return {
        "kind": kind,
        "title": title,
        "summary": f"{title} should be preserved as resident understanding.",
        "reason": "The model said this mattered.",
        "source": {
            "excerpt": excerpt or "Important living idea.",
            "line_start": line_start,
            "line_end": line_end,
        },
        "tags": ["vision"],
    }
