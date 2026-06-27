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

    assert result.packet_ref == "resident/momentum/packets/packet-build-momentum-pipeline.md"
    assert len(result.artifact_refs) == 4
    refs = await state.list_refs("resident/momentum")
    assert result.run_ref in refs
    assert result.packet_ref in refs
    packet = (tmp_path / "state" / result.packet_ref).read_text(encoding="utf-8")
    assert "## Why It Matters" in packet
    assert "## Out Of Scope" in packet
    assert "Mimir" in packet
    artifact = (tmp_path / "state" / result.artifact_refs[0]).read_text(encoding="utf-8")
    assert "- source_path:" in artifact
    assert "- extraction_run_id: run-test" in artifact
    assert "- line_start: 1" in artifact
    assert "The model said this mattered." in artifact


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
    assert first.artifact_refs == second.artifact_refs
    assert first.extraction.model_dump(mode="json") == second.extraction.model_dump(mode="json")


@pytest.mark.asyncio
async def test_vision_fixture_preserves_required_ideas_as_model_artifacts(tmp_path: Path):
    fixture = Path("tests/test_ravn/fixtures/niuu_vision_messy.md")
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(FakeLLM(_vision_payload()), model="fake-model"),
        state=LocalResidentState(tmp_path / "state"),
        now=datetime(2026, 6, 27, 12, tzinfo=UTC),
        run_id="vision-proof",
    ).extract_file(fixture)

    artifacts = [*result.extraction.artifacts, result.extraction.resident_patch]
    titles = {artifact.title for artifact in artifacts}
    assert {
        "Niuu as a Momentum Engine",
        "Drive preserves momentum",
        "Work does not begin with a prompt",
        "Goals are prompting in disguise",
        "Learned reflexes over hardcoded rules",
        "Self-awareness as operational self-modeling",
        "Protect insight from context dilution",
        "Use the selected memory backend",
        "LLM council semantic authority",
        "Autonomy proposal needs reset",
        "Bounded cognitive workers for context hygiene",
        "Responsibility rejected as product language",
        "Avoid mobile-hostile vision cadence",
    } <= titles
    assert all(artifact.reason for artifact in artifacts)
    assert all(
        artifact.provenance.source_path.endswith("niuu_vision_messy.md")
        for artifact in artifacts
    )
    assert result.extraction.packet.out_of_scope
    assert result.extraction.packet.reuse_guidance
    assert result.extraction.packet.reflection_prompts


def test_momentum_extract_cli_runs_pipeline(monkeypatch, tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("messy notes", encoding="utf-8")

    monkeypatch.setattr(commands, "_build_llm", lambda _settings: FakeLLM(_payload()))

    async def _state(_settings, _workspace):
        return LocalResidentState(tmp_path / "state")

    monkeypatch.setattr(commands, "_build_resident_state", _state)

    result = CliRunner().invoke(commands.app, ["momentum", "extract", str(source)])

    assert result.exit_code == 0, result.output
    assert "artifacts:   4" in result.output
    assert (
        "packet_ref:  resident/momentum/packets/packet-build-momentum-pipeline.md"
        in result.output
    )


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
            "source": {"excerpt": "Important living idea.", "line_start": 1, "line_end": 2},
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
            "source": {"excerpt": "Important living idea.", "line_start": 1, "line_end": 2},
        },
    }


def _vision_payload() -> dict:
    titles = [
        (
            "durable_insight",
            "Niuu as a Momentum Engine",
            "Niuu is not just an agent platform; call it a Momentum Engine.",
        ),
        (
            "durable_insight",
            "Drive preserves momentum",
            "Drive is the mechanism behind momentum.",
        ),
        (
            "durable_insight",
            "Work does not begin with a prompt",
            "Work does not begin with a prompt.",
        ),
        (
            "durable_insight",
            "Goals are prompting in disguise",
            "goals are prompting in disguise.",
        ),
        (
            "durable_insight",
            "Learned reflexes over hardcoded rules",
            "Reflexes should be learned from experience instead of hardcoded rules.",
        ),
        (
            "durable_insight",
            "Self-awareness as operational self-modeling",
            "Self-awareness here should mean operational self-modeling",
        ),
        (
            "durable_insight",
            "Protect insight from context dilution",
            "We keep losing the living shape of the idea",
        ),
        (
            "durable_insight",
            "Use the selected memory backend",
            "Use the selected resident memory backend",
        ),
        (
            "durable_insight",
            "LLM council semantic authority",
            "semantic authority should be model or council driven",
        ),
        (
            "unresolved_tension",
            "Autonomy proposal needs reset",
            "it is messy. It probably needs reset",
        ),
        (
            "durable_insight",
            "Bounded cognitive workers for context hygiene",
            "Use bounded cognitive workers or subagents",
        ),
        (
            "rejected_direction",
            "Responsibility rejected as product language",
            '"responsibility" is bad external product language',
        ),
        (
            "rejected_direction",
            "Avoid mobile-hostile vision cadence",
            "mobile-hostile one-line paragraph/list cadence",
        ),
    ]
    payload = _payload()
    payload["artifacts"] = [_artifact(kind, title, excerpt) for kind, title, excerpt in titles]
    payload["packet"]["title"] = "Preserve Niuu vision before execution"
    payload["packet"]["implementation_slice"] = (
        "Implement the first typed Momentum Packet extraction proof only."
    )
    return payload


def _artifact(kind: str, title: str, excerpt: str | None = None) -> dict:
    return {
        "kind": kind,
        "title": title,
        "summary": f"{title} should be preserved as resident understanding.",
        "reason": "The model said this mattered.",
        "source": {"excerpt": excerpt or title, "line_start": 1, "line_end": 1},
        "tags": ["vision"],
    }
