"""Tests for the Valkyrie self-improvement proof loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ravn.cli.commands import app
from ravn.valkyrie_evolution import ValkyrieEvolutionProofRunner
from ravn.valkyrie_evolution.adapters import TemplateToolBuilder
from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest


class UnsafeBuilder:
    async def build(self, request: EvolutionRequest) -> BuildResult:
        skill_name = f"unsafe-{request.gap.gap_id}"
        return BuildResult(
            request_id=request.request_id,
            skill_name=skill_name,
            skill_content=(
                f"# skill: {skill_name}\n\n"
                f"metadata:\n  capability: {request.gap.capability_name}\n\n"
                "Procedure: kubectl delete the affected workload.\n"
            ),
            description="Unsafe generated skill.",
            artifact_type="ravn_skill_tool",
        )


@pytest.mark.asyncio
async def test_evolution_proof_builds_skills_and_uses_them_on_replay(tmp_path: Path) -> None:
    report = await ValkyrieEvolutionProofRunner(
        out_dir=tmp_path,
        builder=TemplateToolBuilder(),
        environment_id="test-env",
        autonomy_mode="yolo",
    ).run()

    assert report.summary["signals_received"] == 6
    assert report.summary["sample_signal_shapes_exercised"] == 3
    assert report.summary["first_pass_decisions"] == 3
    assert report.summary["capability_gaps_detected"] == 3
    assert report.summary["dream_cycles_completed"] == 1
    assert report.summary["skills_built"] == 3
    assert report.summary["odin_reviews"] == 3
    assert report.summary["skills_activated"] == 4
    assert report.summary["local_skills_activated"] == 3
    assert report.summary["resident_skills_installed"] == 1
    assert report.summary["replay_decisions"] == 3
    assert report.summary["skills_used_on_replay"] == 3
    assert report.summary["flock_learnings_proposed"] == 1
    assert report.summary["resident_learnings_adopted"] == 1
    assert report.summary["resident_learnings_rejected"] == 2
    assert report.summary["resident_adopted_skills_used"] == 1
    assert report.summary["resident_odin_decisions"] == 2
    assert report.summary["container_safe_artifacts"] is True
    assert report.summary["hardcoded_tool_choices"] is False

    assert all(decision["capability_gap"] for decision in report.first_pass_decisions)
    assert all(decision["skill_name"] for decision in report.replay_decisions)
    assert {build["artifact_type"] for build in report.build_results} == {"ravn_skill_tool"}
    assert {review["outcome"] for review in report.review_results} == {"yolo_approved"}

    skill_files = sorted((tmp_path / "skills").glob("*.md"))
    assert len(skill_files) == 3
    skill_text = "\n".join(path.read_text(encoding="utf-8") for path in skill_files)
    for decision in report.replay_decisions:
        assert f"capability: {decision['capability_name']}" in skill_text

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event_types = {event["event_type"] for event in events}
    assert "valkyrie.capability_gap.detected" in event_types
    assert "valkyrie.dream.started" in event_types
    assert "valkyrie.dream.completed" in event_types
    assert "valkyrie.evolution.requested" in event_types
    assert "valkyrie.evolution.built" in event_types
    assert "odin.court.decided" in event_types
    assert "valkyrie.evolution.activated" in event_types
    assert "valkyrie.evolution.proven" in event_types
    assert not list(tmp_path.glob("*.db"))


def test_valkyrie_evolution_proof_cli_writes_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    out_dir = tmp_path / "proof"

    result = runner.invoke(
        app,
        [
            "valkyrie-evolution-proof",
            "--out-dir",
            str(out_dir),
            "--environment-id",
            "cli-test-env",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Valkyrie evolution proof complete" in result.output
    assert "odin reviews           : 3" in result.output
    assert "skills activated       : 4" in result.output
    assert "replay skills used     : 3" in result.output
    assert "flock proposals        : 1" in result.output
    assert "resident installs      : 1" in result.output
    assert "resident adopted       : 1" in result.output
    assert "resident rejected      : 2" in result.output
    assert "resident skill uses    : 1" in result.output

    report = json.loads((out_dir / "proof-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["signals_received"] == 6
    assert report["summary"]["skills_built"] == 3
    assert report["summary"]["odin_reviews"] == 3
    assert report["summary"]["skills_used_on_replay"] == 3
    assert report["summary"]["resident_skills_installed"] == 1
    assert (out_dir / "proof-report.md").exists()
    assert (out_dir / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_evolution_proof_is_repeatable_in_same_output_dir(tmp_path: Path) -> None:
    first = await ValkyrieEvolutionProofRunner(
        out_dir=tmp_path,
        builder=TemplateToolBuilder(),
    ).run()
    second = await ValkyrieEvolutionProofRunner(
        out_dir=tmp_path,
        builder=TemplateToolBuilder(),
    ).run()

    assert first.summary["capability_gaps_detected"] == 3
    assert second.summary["capability_gaps_detected"] == 3
    assert second.summary["skills_built"] == 3
    assert second.summary["skills_used_on_replay"] == 3


@pytest.mark.asyncio
async def test_supervised_mode_requires_odin_review_before_activation(tmp_path: Path) -> None:
    report = await ValkyrieEvolutionProofRunner(
        out_dir=tmp_path,
        builder=TemplateToolBuilder(),
        autonomy_mode="supervised",
    ).run()

    assert report.summary["odin_reviews"] == 3
    assert report.summary["odin_reviews_required"] == 3
    assert report.summary["skills_activated"] == 4
    assert report.summary["local_skills_activated"] == 3
    assert report.summary["resident_skills_installed"] == 1
    assert report.summary["skills_used_on_replay"] == 3
    assert all(review["required_for_activation"] for review in report.review_results)


@pytest.mark.asyncio
async def test_odin_review_holds_unsafe_generated_skills(tmp_path: Path) -> None:
    report = await ValkyrieEvolutionProofRunner(
        out_dir=tmp_path,
        builder=UnsafeBuilder(),
        autonomy_mode="supervised",
    ).run()

    assert report.summary["odin_reviews_required"] == 3
    assert report.summary["skills_activated"] == 0
    assert report.summary["skills_held"] == 3
    assert report.summary["skills_used_on_replay"] == 0
    assert {review["outcome"] for review in report.review_results} == {"rejected"}
