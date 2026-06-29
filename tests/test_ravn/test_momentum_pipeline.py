from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.executors.cli import CliTransportExecutor
from ravn.adapters.resident_signal import (
    MarkdownResidentSignalSource,
    MimirResidentInboxSignalSource,
)
from ravn.adapters.resident_state.gbrain import GBrainResidentStateAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState, MimirResidentState
from ravn.cli import commands
from ravn.config import MomentumExecutorConfig
from ravn.domain.models import LLMResponse, StopReason, TokenUsage, ToolCall, ToolResult, TurnResult
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_JUDGMENT_PROPOSED,
    validate_valkyrie_outcome,
)
from ravn.momentum import (
    MomentumAttentionWorker,
    MomentumDelegationWorker,
    MomentumExtractionWorker,
    MomentumPipeline,
    MomentumReflectionWorker,
)
from ravn.momentum.models import (
    MomentumAttentionDecision,
    MomentumDelegationBrief,
    MomentumHandoffResult,
    MomentumStatePatch,
    MomentumStateTension,
)
from ravn.momentum.render import (
    judgment_event_payload,
    parse_delegation_brief,
    render_attention_decision,
    render_delegation_brief,
)
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
from ravn.ports.resident_signal import (
    ResidentSignalCandidateSourcePort,
    ResidentSignalSourcePort,
)
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


class StaticSignalSource:
    def __init__(self, signals: list[ResidentInboxSignal]) -> None:
        self.signals = signals
        self.calls: list[str] = []

    async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal:
        self.calls.append(ref_or_id)
        for signal in self.signals:
            if ref_or_id in {signal.id, signal.raw_ref}:
                return signal
        raise FileNotFoundError(ref_or_id)


class FakeMomentumExecutorAgent:
    def __init__(
        self,
        *,
        status: str = "completed",
        summary: str = "unit/mock executor handled the brief",
        output: str = "unit/mock output",
        follow_up_recommended: str = "reflect",
    ) -> None:
        self.status = status
        self.summary = summary
        self.output = output
        self.follow_up_recommended = follow_up_recommended
        self.calls: list[str] = []
        self.llm_adapter_name = "unit-mock-executor"
        self.task_id = "unit/mock boundary fake"

    async def run_turn(self, user_input: str) -> TurnResult:
        self.calls.append(user_input)
        response = json.dumps(
            {
                "status": self.status,
                "summary": self.summary,
                "output": self.output,
                "evidence_refs": ["resident/evidence/unit-mock.md"],
                "produced_refs": (
                    ["resident/produced/unit-mock.md"] if self.status == "completed" else []
                ),
                "errors": ["unit/mock failure"] if self.status != "completed" else [],
                "follow_up_recommended": self.follow_up_recommended,
            }
        )
        return TurnResult(
            response=response,
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class FakeInvalidExecutorAgent(FakeMomentumExecutorAgent):
    async def run_turn(self, user_input: str) -> TurnResult:
        self.calls.append(user_input)
        return TurnResult(
            response="free text is not a structured handoff result",
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class FakeFencedExecutorAgent(FakeMomentumExecutorAgent):
    async def run_turn(self, user_input: str) -> TurnResult:
        self.calls.append(user_input)
        response = json.dumps(
            {
                "status": "completed",
                "summary": "unit/mock executor used fenced JSON",
                "output": "unit/mock output",
                "evidence_refs": [],
                "produced_refs": ["resident/produced/unit-mock.md"],
                "errors": [],
                "follow_up_recommended": "none",
            }
        )
        return TurnResult(
            response=f"```json\n{response}\n```",
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class FakeTraceExecutorAgent(FakeMomentumExecutorAgent):
    async def run_turn(self, user_input: str) -> TurnResult:
        self.calls.append(user_input)
        response = json.dumps(
            {
                "status": "completed",
                "summary": "unit/mock executor reported an existing trace",
                "output": "unit/mock output",
                "evidence_refs": ["resident/evidence/unit-mock.md"],
                "produced_refs": ["resident/produced/unit-mock.md"],
                "errors": [],
                "follow_up_recommended": "none",
            }
        )
        return TurnResult(
            response=response,
            tool_calls=[
                ToolCall(id="call-1", name="Read", input={"file_path": "state/ref.md"})
            ],
            tool_results=[
                ToolResult(tool_call_id="call-1", content="read result", is_error=False)
            ],
            usage=TokenUsage(input_tokens=11, output_tokens=7, cache_read_tokens=3),
        )


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


def _attention_decision(**updates) -> MomentumAttentionDecision:
    payload = _attention_payload()
    payload.update({k: v for k, v in updates.items() if k != "validation_status"})
    return MomentumAttentionDecision(
        **payload,
        decision_id="attention-test",
        validation_status=updates.get("validation_status", "valid"),
        created_at=datetime(2026, 6, 27, 13, tzinfo=UTC),
        current_state_ref=CURRENT_MOMENTUM_STATE_REF,
        current_state_present=True,
        candidate_count=1,
        candidate_limit=1,
        candidates_truncated=0,
        procedure_name="momentum_attention_v1",
        model_name="fake-model",
    )


def _delegation_payload(**updates) -> dict:
    payload = {
        "handoff_recommended": True,
        "no_handoff_reason": "",
        "title": "Prepare a bounded implementation slice",
        "rationale": "The judgment asks for a bounded code change that needs a coding agent.",
        "desired_outcome": "A focused follow-up implementation with tests.",
        "bounded_request": "Implement only the smallest verified Momentum follow-up.",
        "evidence_refs": [
            "resident/momentum/runs/run-delegate/run.md",
            "resident/momentum/runs/run-delegate/judgment/judgment-attend-to-momentum-dilution.md",
        ],
        "constraints": [
            "Use the source judgment as evidence.",
            "Do not execute external workflows.",
        ],
        "out_of_scope_boundaries": [
            "Do not execute external workflows.",
            "Do not register capabilities.",
        ],
        "success_proof": "The implementation diff includes relevant tests.",
        "expected_return_format": "Summarize changed files, validation, and residual risk.",
        "suggested_executor_context": "local Codex session",
        "skill_or_tool_hints": ["Use normal repository tests."],
        "capability_gap_notes": [],
        "handoff_notes": "Keep the handoff bounded and auditable.",
        "confidence": 0.81,
        "execution_performed": False,
    }
    payload.update(updates)
    return payload


async def _seed_linked_momentum_run(
    state: LocalResidentState,
    *,
    run_id: str = "run-delegate",
) -> tuple[str, str, str]:
    attention_ref = await state.write_artifact(
        "resident/continuation/momentum/attention/attention-test.md",
        render_attention_decision(_attention_decision()),
    )
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 14, tzinfo=UTC),
        run_id=run_id,
    ).pursue_attention(
        attention_ref,
        signal_source=StaticSignalSource(
            [_candidate("sig-relevant", "Relevant signal", "Important living idea.")]
        ),
    )
    return result.run_ref, result.judgment_ref, attention_ref


async def _seed_delegation_brief(
    state: LocalResidentState,
    **payload_updates,
) -> tuple[str, MomentumDelegationBrief]:
    run_ref, _, _ = await _seed_linked_momentum_run(state)
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(_delegation_payload(**payload_updates)),
            model="fake-model",
        ),
        state=state,
        now=datetime(2026, 6, 27, 15, tzinfo=UTC),
    ).prepare_delegation(run_ref)
    return result.brief_ref, result.brief


def test_momentum_delegation_proof_seed_script_replays_committed_fixtures(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).parent
        / "fixtures"
        / "scripts"
        / "seed_momentum_delegation_proof.py"
    )
    proof_root = tmp_path / "proof"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(proof_root)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"proof_root: {proof_root}" in result.stdout
    assert "current_state_ref: resident/continuation/momentum/state/current.md" in result.stdout
    assert "candidate_id: sig-attention-current-state-relevant" in result.stdout
    assert "candidate_id: sig-attention-distractor" in result.stdout
    assert (
        proof_root / "state" / "resident/continuation/momentum/state/current.md"
    ).exists()
    assert (
        proof_root
        / "mimir/wiki/resident/inbox/signals/20260628T100500Z-current-state-attention.md"
    ).exists()


def test_momentum_handoff_proof_seed_script_writes_only_state_and_signals(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).parent
        / "fixtures"
        / "scripts"
        / "seed_momentum_handoff_proof.py"
    )
    proof_root = tmp_path / "proof"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(proof_root)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "current_state_ref: resident/continuation/momentum/state/current.md" in result.stdout
    assert "candidate_id: sig-attention-current-state-relevant" in result.stdout
    assert "candidate_id: sig-attention-distractor" in result.stdout
    assert "attention_ref:" not in result.stdout
    assert "run_ref:" not in result.stdout
    assert "judgment_ref:" not in result.stdout
    assert "brief_ref:" not in result.stdout

    assert (
        proof_root / "state" / "resident/continuation/momentum/state/current.md"
    ).exists()
    assert (
        proof_root
        / "mimir/wiki/resident/inbox/signals/20260628T100500Z-current-state-attention.md"
    ).exists()
    assert (
        proof_root
        / "state/resident/inbox/signals/20260628T100500Z-current-state-attention.md"
    ).exists()
    assert (
        proof_root
        / "mimir/wiki/resident/inbox/signals/20260628T100400Z-distractor.md"
    ).exists()
    assert (
        proof_root
        / "state/resident/inbox/signals/20260628T100400Z-distractor.md"
    ).exists()

    forbidden = [
        "state/resident/continuation/momentum/attention",
        "state/resident/momentum/runs",
        "state/resident/continuation/momentum/delegations",
        "state/resident/continuation/momentum/handoffs",
        "state/resident/continuation/momentum/handoffs/evidence",
        "executor-reports",
    ]
    for relative in forbidden:
        assert not (proof_root / relative).exists(), relative


def test_real_llm_proof_helpers_do_not_force_semantic_answers() -> None:
    script = (
        Path(__file__).parent
        / "fixtures"
        / "scripts"
        / "ollama_momentum_delegation_proof_llm.py"
    )
    source = script.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_const_paths = {("properties", "execution_performed", "const")}
    semantic_strings = [
        "sig-attention-current-state-relevant",
        "resident/inbox/signals/20260628T100500Z-current-state-attention.md",
        "Current-state attention signal addresses open tension",
        "Resident understanding patch",
        "Attend to current-state signal",
        "Momentum Handoff Brief",
        "select this exact",
        "return this exact",
    ]

    const_paths: list[tuple[str, ...]] = []

    def walk(node: ast.AST, path: tuple[str, ...] = ()) -> None:
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                key_value = key.value if isinstance(key, ast.Constant) else None
                next_path = (*path, str(key_value)) if isinstance(key_value, str) else path
                if key_value == "const":
                    const_paths.append(next_path)
                walk(value, next_path)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, path)

    walk(tree)

    assert set(const_paths) <= allowed_const_paths
    assert all(text not in source for text in semantic_strings)
    assert "raise ValueError(\"unrecognized Momentum proof prompt\")" in source


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
        "Claude",
        "Codex",
        "MimirResidentInbox",
        "GBrainResidentStateAdapter",
        "GitHub",
        "Skuld",
        "Sleipnir",
        "printer",
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
async def test_momentum_pursues_attention_decision_into_linked_run(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    attention_ref = await state.write_artifact(
        "resident/continuation/momentum/attention/attention-test.md",
        render_attention_decision(_attention_decision()),
    )
    attention_before = (await state.read_artifact(attention_ref)).content
    signal = _candidate("sig-relevant", "Relevant signal", "Important living idea.")
    source = StaticSignalSource([signal])

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 14, tzinfo=UTC),
        run_id="pursue-linked",
    ).pursue_attention(attention_ref, signal_source=source)

    run = result.extraction.run
    assert source.calls == ["resident/inbox/signals/sig-relevant.md"]
    assert run.attention_ref == attention_ref
    assert run.attention_decision_id == "attention-test"
    assert run.selected_signal_id == "sig-relevant"
    assert run.selected_signal_ref == "resident/inbox/signals/sig-relevant.md"
    assert result.provenance_fully_verified is True

    run_content = (await state.read_artifact(result.run_ref)).content
    assert f"attention_ref: {attention_ref}" in run_content
    assert "attention_decision_id: attention-test" in run_content
    assert "selected_signal_id: sig-relevant" in run_content
    assert "selected_signal_ref: resident/inbox/signals/sig-relevant.md" in run_content
    assert (await state.read_artifact(attention_ref)).content == attention_before

    refs = await state.list_refs()
    assert not any("/reflections/" in ref for ref in refs)
    assert not any("delegation" in ref or "execution" in ref for ref in refs)


@pytest.mark.asyncio
async def test_momentum_pursuit_parses_attention_decision_data_block(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    content = render_attention_decision(_attention_decision()).replace(
        "- recommended_next_action: extract_selected_signal",
        "- recommended_next_action: ask_human",
        1,
    )
    attention_ref = await state.write_artifact(
        "resident/continuation/momentum/attention/attention-test.md",
        content,
    )
    signal = _candidate("sig-relevant", "Relevant signal", "Important living idea.")
    source = StaticSignalSource([signal])

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 14, tzinfo=UTC),
        run_id="pursue-decision-data",
    ).pursue_attention(attention_ref, signal_source=source)

    assert source.calls == ["resident/inbox/signals/sig-relevant.md"]
    assert result.extraction.run.attention_decision_id == "attention-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "match"),
    [
        (
            {
                "selected_signal_id": None,
                "selected_signal_ref": None,
                "no_attention_needed": True,
                "recommended_next_action": "no_action",
                "selected_tension_ids": [],
                "signal_refs": ["none"],
            },
            "no attention is needed",
        ),
        (
            {"recommended_next_action": "ask_human"},
            "does not recommend extracting",
        ),
        (
            {"selected_signal_id": None, "selected_signal_ref": None},
            "did not select a signal",
        ),
        (
            {"validation_status": "invalid"},
            "not valid",
        ),
    ],
)
async def test_momentum_rejects_non_pursuable_attention_decisions(
    tmp_path: Path,
    updates: dict,
    match: str,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    attention_ref = await state.write_artifact(
        "resident/continuation/momentum/attention/attention-test.md",
        render_attention_decision(_attention_decision(**updates)),
    )
    source = StaticSignalSource([_candidate("sig-relevant", "Relevant", "content")])

    with pytest.raises(ValueError, match=match):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            state=state,
        ).pursue_attention(attention_ref, signal_source=source)

    assert source.calls == []
    assert await state.list_refs("resident/momentum/runs") == []


@pytest.mark.asyncio
async def test_momentum_pursuit_missing_selected_signal_creates_no_run(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    attention_ref = await state.write_artifact(
        "resident/continuation/momentum/attention/attention-test.md",
        render_attention_decision(_attention_decision()),
    )
    source = StaticSignalSource([])

    with pytest.raises(ValueError, match="selected signal not found"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            state=state,
        ).pursue_attention(attention_ref, signal_source=source)

    assert source.calls == ["resident/inbox/signals/sig-relevant.md"]
    assert await state.list_refs("resident/momentum/runs") == []


@pytest.mark.asyncio
async def test_momentum_delegates_judgment_into_persisted_brief(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    run_ref, judgment_ref, attention_ref = await _seed_linked_momentum_run(state)
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(_delegation_payload()),
            model="fake-model",
        ),
        state=state,
        now=datetime(2026, 6, 27, 15, tzinfo=UTC),
    ).prepare_delegation(judgment_ref)

    brief = result.brief
    assert brief.source_judgment_ref == judgment_ref
    assert brief.source_run_ref == run_ref
    assert brief.source_attention_ref == attention_ref
    assert brief.source_signal_id == "sig-relevant"
    assert brief.source_signal_ref == "resident/inbox/signals/sig-relevant.md"
    assert brief.handoff_recommended is True
    assert brief.suggested_executor_context == "local Codex session"
    assert brief.execution_performed is False

    content = (await state.read_artifact(result.brief_ref)).content
    parsed = parse_delegation_brief(content)
    assert parsed.brief_id == brief.brief_id
    assert "## Brief Data" in content
    assert "## Bounded Request" in content
    assert f"- source_attention_ref: {attention_ref}" in content


@pytest.mark.asyncio
async def test_momentum_delegates_run_by_resolving_judgment(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    run_ref, judgment_ref, _ = await _seed_linked_momentum_run(state)

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(_delegation_payload()),
            model="fake-model",
        ),
        state=state,
        now=datetime(2026, 6, 27, 15, tzinfo=UTC),
    ).prepare_delegation(run_ref)

    assert result.brief.source_run_ref == run_ref
    assert result.brief.source_judgment_ref == judgment_ref


@pytest.mark.asyncio
async def test_momentum_delegation_no_delegation_needed_persists(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    _, judgment_ref, _ = await _seed_linked_momentum_run(state)

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(
                _delegation_payload(
                    handoff_recommended=False,
                    no_handoff_reason="The judgment only needs resident memory to remain updated.",
                    title="No delegation needed",
                    rationale="The judgment only needs resident memory to remain updated.",
                    desired_outcome="No external handoff is prepared.",
                    bounded_request="No external delegation should be prepared.",
                    success_proof="The persisted brief records why no handoff is needed.",
                    expected_return_format="No executor response expected.",
                    suggested_executor_context="",
                    handoff_notes="No handoff recommended.",
                )
            ),
            model="fake-model",
        ),
        state=state,
    ).prepare_delegation(judgment_ref)

    assert result.brief.handoff_recommended is False
    assert result.brief.no_handoff_reason
    assert result.brief.execution_performed is False
    refs = await state.list_refs()
    assert not any("/reflections/" in ref or "/dispositions/" in ref for ref in refs)


@pytest.mark.asyncio
async def test_momentum_delegation_accepts_free_text_executor_context_without_catalog(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    _, judgment_ref, _ = await _seed_linked_momentum_run(state)

    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(
                _delegation_payload(
                    suggested_executor_context="unlisted future executor",
                    skill_or_tool_hints=["executor native skills decide availability"],
                )
            ),
            model="fake-model",
        ),
        state=state,
    ).prepare_delegation(judgment_ref)

    assert result.brief.suggested_executor_context == "unlisted future executor"
    assert result.brief.skill_or_tool_hints == ["executor native skills decide availability"]


@pytest.mark.asyncio
async def test_momentum_delegation_rejects_execution_performed(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    _, judgment_ref, _ = await _seed_linked_momentum_run(state)

    with pytest.raises(ValueError, match="delegation brief execution must be false"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            delegation_worker=MomentumDelegationWorker(
                FakeLLM(_delegation_payload(execution_performed=True)),
                model="fake-model",
            ),
            state=state,
        ).prepare_delegation(judgment_ref)

    assert await state.list_refs("resident/continuation/momentum/delegations") == []


@pytest.mark.asyncio
async def test_momentum_delegation_missing_source_fails_without_artifact(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    with pytest.raises(FileNotFoundError, match="Momentum judgment or run not found"):
        await MomentumPipeline(
            worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
            delegation_worker=MomentumDelegationWorker(
                FakeLLM(_delegation_payload()),
                model="fake-model",
            ),
            state=state,
        ).prepare_delegation("resident/momentum/runs/missing/run.md")

    assert await state.list_refs("resident/continuation/momentum/delegations") == []


@pytest.mark.asyncio
async def test_momentum_delegation_leaves_source_artifacts_immutable(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    run_ref, judgment_ref, attention_ref = await _seed_linked_momentum_run(state)
    before = {
        ref: (await state.read_artifact(ref)).content
        for ref in [run_ref, judgment_ref, attention_ref]
    }

    await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        delegation_worker=MomentumDelegationWorker(
            FakeLLM(_delegation_payload()),
            model="fake-model",
        ),
        state=state,
    ).prepare_delegation(judgment_ref)

    after = {
        ref: (await state.read_artifact(ref)).content
        for ref in [run_ref, judgment_ref, attention_ref]
    }
    assert after == before


@pytest.mark.asyncio
async def test_momentum_handoff_unit_mock_persists_linked_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, brief = await _seed_delegation_brief(state)
    executor = FakeMomentumExecutorAgent()

    result = await MomentumPipeline(
        state=state,
        now=datetime(2026, 6, 27, 16, tzinfo=UTC),
    ).handoff_delegation(
        brief_ref,
        executor=executor,
        signal_source=StaticSignalSource(
            [_candidate("sig-relevant", "Relevant signal", "Important living idea.")]
        ),
    )

    assert result.result_ref.startswith("resident/continuation/momentum/handoffs/")
    assert result.result.source_brief_ref == brief_ref
    assert result.result.source_brief_id == brief.brief_id
    assert result.result.source_run_ref == brief.source_run_ref
    assert result.result.source_judgment_ref == brief.source_judgment_ref
    assert result.result.source_attention_ref == brief.source_attention_ref
    assert result.result.source_signal_id == "sig-relevant"
    assert result.result.source_signal_ref == "resident/inbox/signals/sig-relevant.md"
    assert result.result.executor_label == "unit-mock-executor"
    assert result.result.status == "completed"
    assert result.result.produced_refs == ["resident/produced/unit-mock.md"]
    assert result.result.follow_up_recommended == "reflect"
    assert result.result.executor_trace_ref.startswith(
        "resident/continuation/momentum/handoffs/traces/"
    )
    rendered = await state.read_artifact(result.result_ref)
    parsed = MomentumHandoffResult.model_validate_json(
        rendered.content.split("```json\n", 1)[1].split("\n```", 1)[0]
    )
    assert parsed.source_brief_ref == brief_ref
    assert parsed.source_signal_id == "sig-relevant"
    assert parsed.executor_trace_ref == result.result.executor_trace_ref
    trace = (await state.read_artifact(result.result.executor_trace_ref)).content
    assert "- tool_call_count: 0" in trace
    assert "- tool_result_count: 0" in trace
    assert '"input_tokens":' not in trace
    assert "unit/mock output" in trace
    assert "Source Judgment" in executor.calls[0]
    assert "Source Attention Decision" in executor.calls[0]
    assert "Selected Signal" in executor.calls[0]
    assert "Current Momentum State" in executor.calls[0]
    preamble = executor.calls[0].split("## Delegation Brief", 1)[0]
    assert "Follow the delegation brief." in preamble
    assert "native tools and permissions" in preamble
    assert "Do not mutate Momentum current state" not in preamble
    assert "promote reflexes" not in preamble
    assert "register capabilities" not in preamble
    assert "schedule follow-up loops" not in preamble
    assert "continue automatically" not in preamble


@pytest.mark.asyncio
async def test_momentum_handoff_persists_existing_executor_turn_trace(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)

    result = await MomentumPipeline(state=state).handoff_delegation(
        brief_ref,
        executor=FakeTraceExecutorAgent(),
    )

    trace = (await state.read_artifact(result.result.executor_trace_ref)).content
    assert "- tool_call_count: 1" in trace
    assert "- tool_result_count: 1" in trace
    assert "- input_tokens: 11" in trace
    assert "- output_tokens: 7" in trace
    assert "- cache_read_tokens: 3" in trace
    assert '"name": "Read"' in trace
    assert '"file_path": "state/ref.md"' in trace
    assert '"tool_call_id": "call-1"' in trace
    assert '"content": "read result"' in trace


@pytest.mark.asyncio
async def test_momentum_handoff_non_handoff_brief_creates_no_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(
        state,
        handoff_recommended=False,
        no_handoff_reason="The judgment needs no executor.",
    )

    with pytest.raises(ValueError, match="not handoffable"):
        await MomentumPipeline(state=state).handoff_delegation(
            brief_ref,
            executor=FakeMomentumExecutorAgent(),
        )

    assert await state.list_refs("resident/continuation/momentum/handoffs") == []


@pytest.mark.asyncio
async def test_momentum_handoff_missing_brief_ref_fails_clearly(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    with pytest.raises(FileNotFoundError, match="delegation brief not found"):
        await MomentumPipeline(state=state).handoff_delegation(
            "resident/continuation/momentum/delegations/missing.md",
            executor=FakeMomentumExecutorAgent(),
        )

    assert await state.list_refs("resident/continuation/momentum/handoffs") == []


@pytest.mark.asyncio
async def test_momentum_handoff_execution_performed_brief_creates_no_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)
    content = (await state.read_artifact(brief_ref)).content.replace(
        '"execution_performed": false',
        '"execution_performed": true',
    )
    bad_ref = await state.write_artifact(
        "resident/continuation/momentum/delegations/executed.md",
        content,
    )

    with pytest.raises(ValueError, match="delegation brief execution must be false"):
        await MomentumPipeline(state=state).handoff_delegation(
            bad_ref,
            executor=FakeMomentumExecutorAgent(),
        )

    assert await state.list_refs("resident/continuation/momentum/handoffs") == []


@pytest.mark.asyncio
async def test_momentum_handoff_missing_source_ref_fails_without_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    _, brief = await _seed_delegation_brief(state)
    bad_brief = brief.model_copy(
        update={"source_judgment_ref": "resident/momentum/runs/missing/judgment/missing.md"}
    )
    bad_ref = await state.write_artifact(
        "resident/continuation/momentum/delegations/missing-source.md",
        render_delegation_brief(bad_brief),
    )

    with pytest.raises(FileNotFoundError, match="delegation source judgment not found"):
        await MomentumPipeline(state=state).handoff_delegation(
            bad_ref,
            executor=FakeMomentumExecutorAgent(),
        )

    assert await state.list_refs("resident/continuation/momentum/handoffs") == []


@pytest.mark.asyncio
async def test_momentum_handoff_missing_selected_signal_fails_without_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)

    with pytest.raises(ValueError, match="delegation source signal not found"):
        await MomentumPipeline(state=state).handoff_delegation(
            brief_ref,
            executor=FakeMomentumExecutorAgent(),
            signal_source=StaticSignalSource([]),
        )

    assert await state.list_refs("resident/continuation/momentum/handoffs") == []


@pytest.mark.asyncio
async def test_momentum_handoff_executor_failure_persists_failed_result(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)

    result = await MomentumPipeline(state=state).handoff_delegation(
        brief_ref,
        executor=FakeMomentumExecutorAgent(
            status="failed",
            summary="unit/mock executor failed",
            output="",
            follow_up_recommended="ask_human",
        ),
    )

    assert result.result.status == "failed"
    assert result.result.errors == ["unit/mock failure"]
    assert result.result.follow_up_recommended == "ask_human"
    assert await state.read_artifact(result.result_ref)


@pytest.mark.asyncio
async def test_momentum_handoff_invalid_structured_executor_output_is_blocked(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)

    result = await MomentumPipeline(state=state).handoff_delegation(
        brief_ref,
        executor=FakeInvalidExecutorAgent(),
    )

    assert result.result.status == "blocked"
    assert result.result.follow_up_recommended == "ask_human"
    assert result.result.output == "free text is not a structured handoff result"
    assert result.result.produced_refs == []
    assert result.result.evidence_refs == []
    assert result.result.executor_trace_ref
    assert result.result.errors
    assert result.result.raw_metadata["raw_output"] == (
        "free text is not a structured handoff result"
    )
    assert "parse_error" in result.result.raw_metadata
    trace = (await state.read_artifact(result.result.executor_trace_ref)).content
    assert "free text is not a structured handoff result" in trace


@pytest.mark.asyncio
async def test_momentum_handoff_fenced_json_executor_output_is_blocked(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, _ = await _seed_delegation_brief(state)

    result = await MomentumPipeline(state=state).handoff_delegation(
        brief_ref,
        executor=FakeFencedExecutorAgent(),
    )

    assert result.result.status == "blocked"
    assert result.result.produced_refs == []
    assert "parse_error" in result.result.raw_metadata


@pytest.mark.asyncio
async def test_momentum_handoff_leaves_sources_and_state_immutable(
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    brief_ref, brief = await _seed_delegation_brief(state)
    refs = [
        brief_ref,
        brief.source_run_ref,
        brief.source_judgment_ref,
        brief.source_attention_ref,
        CURRENT_MOMENTUM_STATE_REF,
    ]
    before = {
        ref: (await state.read_artifact(ref)).content
        for ref in refs
        if ref
    }
    patches_before = await state.list_refs("resident/continuation/momentum/state/patches")

    await MomentumPipeline(state=state).handoff_delegation(
        brief_ref,
        executor=FakeMomentumExecutorAgent(),
    )

    after = {
        ref: (await state.read_artifact(ref)).content
        for ref in refs
        if ref
    }
    assert after == before
    assert (
        await state.list_refs("resident/continuation/momentum/state/patches")
    ) == patches_before
    refs_after = await state.list_refs("resident/continuation/momentum")
    assert not any("/reflections/" in ref or "/dispositions/" in ref for ref in refs_after)


def test_momentum_core_does_not_import_concrete_delegation_implementations() -> None:
    core_files = [
        Path("src/ravn/momentum/pipeline.py"),
        Path("src/ravn/momentum/worker.py"),
        Path("src/ravn/momentum/render.py"),
    ]
    forbidden = (
        "ravn.adapters",
        "github",
        "shell",
        "printer",
        "provider",
        "sleipnir",
    )

    imports = [
        line
        for path in core_files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]

    assert not any(token in line.lower() for line in imports for token in forbidden)


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
    source: ResidentSignalCandidateSourcePort = StaticCandidateSource(
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


def test_momentum_pursue_cli_prints_linked_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    attention_ref = asyncio.run(
        state.write_artifact(
            "resident/continuation/momentum/attention/attention-test.md",
            render_attention_decision(_attention_decision()),
        )
    )
    source = StaticSignalSource(
        [_candidate("sig-relevant", "Relevant signal", "Important living idea.")]
    )
    monkeypatch.setattr(commands, "_build_resident_inbox_signal_source", lambda _s: source)
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "pursue", attention_ref])

    assert result.exit_code == 0, result.output
    assert f"attention_ref: {attention_ref}" in result.output
    assert "selected_signal_id: sig-relevant" in result.output
    assert "selected_signal_ref: resident/inbox/signals/sig-relevant.md" in result.output
    assert "run_ref: resident/momentum/runs/" in result.output
    assert "judgment_ref: resident/momentum/runs/" in result.output
    assert "packet_ref: resident/momentum/runs/" in result.output
    assert f"current_state_ref: {CURRENT_MOMENTUM_STATE_REF}" in result.output
    assert "state_patch_ref: resident/continuation/momentum/state/patches/" in result.output
    assert "provenance: verified" in result.output
    assert source.calls == ["resident/inbox/signals/sig-relevant.md"]


def test_momentum_pursue_cli_invalid_attention_ref_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    monkeypatch.setattr(
        commands,
        "_build_resident_inbox_signal_source",
        lambda _s: StaticSignalSource([]),
    )
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        ["momentum", "pursue", "resident/continuation/momentum/attention/missing.md"],
    )

    assert result.exit_code == 1
    assert "attention decision not found" in result.output


def test_momentum_pursue_cli_non_pursuable_decision_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    attention_ref = asyncio.run(
        state.write_artifact(
            "resident/continuation/momentum/attention/attention-test.md",
            render_attention_decision(
                _attention_decision(
                    selected_signal_id=None,
                    selected_signal_ref=None,
                    no_attention_needed=True,
                    recommended_next_action="no_action",
                    selected_tension_ids=[],
                    signal_refs=["none"],
                )
            ),
        )
    )
    monkeypatch.setattr(
        commands,
        "_build_resident_inbox_signal_source",
        lambda _s: StaticSignalSource([]),
    )
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "pursue", attention_ref])

    assert result.exit_code == 1
    assert "Cannot pursue attention decision: attention decision says no attention" in result.output
    assert asyncio.run(state.list_refs("resident/momentum/runs")) == []


def test_momentum_delegate_cli_prints_brief_and_source_refs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        run_ref, _, _ = await _seed_linked_momentum_run(state)
        return run_ref

    run_ref = asyncio.run(_seed())
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_delegation_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "delegate", run_ref])

    assert result.exit_code == 0, result.output
    assert "brief_ref: resident/continuation/momentum/delegations/" in result.output
    assert "source_judgment_ref: resident/momentum/runs/run-delegate/judgment/" in result.output
    assert f"source_run_ref: {run_ref}" in result.output
    assert (
        "source_attention_ref: resident/continuation/momentum/attention/attention-test.md"
        in result.output
    )
    assert "source_signal_id: sig-relevant" in result.output
    assert "source_signal_ref: resident/inbox/signals/sig-relevant.md" in result.output
    assert "handoff_recommended: true" in result.output
    assert "suggested_executor_context: local Codex session" in result.output
    assert "confidence: 0.81" in result.output
    assert "execution_performed: false" in result.output


def test_momentum_delegate_cli_invalid_source_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_delegation_payload()))

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        ["momentum", "delegate", "resident/momentum/runs/missing/run.md"],
    )

    assert result.exit_code == 1
    assert "Momentum judgment or run not found" in result.output


def test_momentum_delegate_cli_execution_performed_creates_no_brief(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        _, judgment_ref, _ = await _seed_linked_momentum_run(state)
        return judgment_ref

    judgment_ref = asyncio.run(_seed())
    monkeypatch.setattr(
        commands,
        "_build_llm",
        lambda _settings: FakeLLM(_delegation_payload(execution_performed=True)),
    )

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "delegate", judgment_ref])

    assert result.exit_code == 1
    assert "delegation brief execution must be false" in result.output
    assert asyncio.run(
        state.list_refs("resident/continuation/momentum/delegations")
    ) == []


def test_momentum_delegate_cli_accepts_unlisted_executor_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        _, judgment_ref, _ = await _seed_linked_momentum_run(state)
        return judgment_ref

    judgment_ref = asyncio.run(_seed())
    monkeypatch.setattr(
        commands,
        "_build_llm",
        lambda _settings: FakeLLM(
            _delegation_payload(
                suggested_executor_context="operator with native tools",
                skill_or_tool_hints=["ask the executor runtime what it supports"],
            )
        ),
    )

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "delegate", judgment_ref])

    assert result.exit_code == 0, result.output
    assert "brief_ref: resident/continuation/momentum/delegations/" in result.output
    assert "suggested_executor_context: operator with native tools" in result.output


def test_momentum_handoff_cli_prints_result_status_and_linkage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        brief_ref, _ = await _seed_delegation_brief(state)
        return brief_ref

    brief_ref = asyncio.run(_seed())
    source = StaticSignalSource(
        [_candidate("sig-relevant", "Relevant signal", "Important living idea.")]
    )
    monkeypatch.setattr(
        commands,
        "_build_momentum_executor_agent",
        lambda _s, _w: FakeMomentumExecutorAgent(),
    )
    monkeypatch.setattr(commands, "_build_optional_resident_inbox_signal_source", lambda _s: source)

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "handoff", brief_ref])

    assert result.exit_code == 0, result.output
    assert "handoff_result_ref: resident/continuation/momentum/handoffs/" in result.output
    assert f"brief_ref: {brief_ref}" in result.output
    assert "executor_label: unit-mock-executor" in result.output
    assert "status: completed" in result.output
    assert "source_run_ref: resident/momentum/runs/run-delegate/run.md" in result.output
    assert "source_judgment_ref: resident/momentum/runs/run-delegate/judgment/" in result.output
    assert (
        "source_attention_ref: resident/continuation/momentum/attention/attention-test.md"
        in result.output
    )
    assert "source_signal_id: sig-relevant" in result.output
    assert "source_signal_ref: resident/inbox/signals/sig-relevant.md" in result.output
    assert "executor_trace_ref: resident/continuation/momentum/handoffs/traces/" in result.output
    assert "produced_refs: resident/produced/unit-mock.md" in result.output
    assert "follow_up_recommended: reflect" in result.output
    assert source.calls == ["resident/inbox/signals/sig-relevant.md"]


def test_momentum_handoff_uses_existing_cli_transport_executor_config() -> None:
    settings = SimpleNamespace(momentum_executor=MomentumExecutorConfig())

    executor = commands._build_momentum_executor(settings)

    assert isinstance(executor, CliTransportExecutor)


def test_momentum_handoff_cli_invalid_brief_ref_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")
    monkeypatch.setattr(
        commands,
        "_build_momentum_executor_agent",
        lambda _s, _w: FakeMomentumExecutorAgent(),
    )
    monkeypatch.setattr(commands, "_build_optional_resident_inbox_signal_source", lambda _s: None)

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(
        commands.app,
        ["momentum", "handoff", "resident/continuation/momentum/delegations/missing.md"],
    )

    assert result.exit_code == 1
    assert "delegation brief not found" in result.output


def test_momentum_handoff_cli_non_handoff_brief_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = LocalResidentState(tmp_path / "state")

    async def _seed() -> str:
        brief_ref, _ = await _seed_delegation_brief(
            state,
            handoff_recommended=False,
            no_handoff_reason="No executor is needed.",
        )
        return brief_ref

    brief_ref = asyncio.run(_seed())
    monkeypatch.setattr(
        commands,
        "_build_momentum_executor_agent",
        lambda _s, _w: FakeMomentumExecutorAgent(),
    )
    monkeypatch.setattr(commands, "_build_optional_resident_inbox_signal_source", lambda _s: None)

    async def _state(_settings, _workspace):
        return state

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "handoff", brief_ref])

    assert result.exit_code == 1
    assert "Cannot hand off delegation brief: delegation brief is not handoffable" in result.output
    assert asyncio.run(
        state.list_refs("resident/continuation/momentum/handoffs")
    ) == []


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
