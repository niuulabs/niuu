from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.cli import commands
from ravn.domain.models import LLMResponse, StopReason, TokenUsage
from ravn.momentum import MomentumExtractionWorker, MomentumPipeline


class FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def generate(self, messages, *, tools, system, model, max_tokens, thinking=None):
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
            content=json.dumps(self.payload),
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=20),
        )

    def stream(self, *args, **kwargs):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_momentum_pipeline_persists_typed_artifacts_with_selected_state(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")
    state = LocalResidentState(tmp_path / "state")
    pipeline = MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=state,
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="run-test",
    )

    result = await pipeline.extract_file(source)

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
    assert "- line_start: 3" in artifact
    assert "- provenance_status: verified" in artifact
    assert "The model said this mattered." in artifact
    judgment = (tmp_path / "state" / result.judgment_ref).read_text(encoding="utf-8")
    assert "- event_type: valkyrie.judgment.proposed" in judgment
    assert "## Tension That Matters" in judgment
    assert "Signal compression is diluting resident understanding." in judgment


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
    ).extract_file(source)

    assert result.packet_ref is None
    assert result.extraction.packet is None
    assert result.extraction.judgment.recommended_next_action == "update_understanding_only"
    run = (tmp_path / "state" / result.run_ref).read_text(encoding="utf-8")
    assert "- judgment_ref: resident/momentum/runs/no-packet/judgment/" in run
    assert "- packet_ref: -" in run


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
    ).extract_file(source)
    second = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "two"),
        now=now,
        run_id="run-replay",
    ).extract_file(source)

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
    ).extract_file(source)
    second = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, 1, tzinfo=UTC),
    ).extract_file(source)

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
    ).extract_file(source)

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
    ).extract_file(fixture)

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

    async def _state(_settings, _workspace):
        return LocalResidentState(tmp_path / "state")

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


def test_momentum_eval_skips_without_opt_in(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")

    result = CliRunner().invoke(commands.app, ["momentum", "eval", str(source)])

    assert result.exit_code == 0
    assert "Skipped: set RAVN_LLM_EVAL=1" in result.output


def test_momentum_eval_runs_when_opted_in(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Messy notes\n\nImportant living idea.", encoding="utf-8")

    monkeypatch.setenv("RAVN_LLM_EVAL", "1")
    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return LocalResidentState(tmp_path / "state")

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "eval", str(source)])

    assert result.exit_code == 0, result.output
    assert "eval:        ok" in result.output
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
            "source": {"excerpt": "Important living idea.", "line_start": 3, "line_end": 3},
        },
        "judgment": {
            "title": "Attend to momentum dilution",
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
            "confidence": 0.82,
            "evidence_artifact_titles": [
                "Niuu as a Momentum Engine",
                "Autonomy proposal needs reset",
            ],
            "source": {"excerpt": "Important living idea.", "line_start": 3, "line_end": 3},
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
            "source": {"excerpt": "Important living idea.", "line_start": 3, "line_end": 3},
        },
    }


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
        "confidence": 0.84,
        "evidence_artifact_titles": [
            "Protect insight from context dilution",
            "Autonomy proposal needs reset",
        ],
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
    line_start: int | None = 3,
    line_end: int | None = 3,
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
