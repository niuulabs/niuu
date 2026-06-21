from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from ravn.adapters.review.command import CommandResidentVerificationAdapter
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.domain.resident_review import (
    ResidentReviewDecision,
    ResidentReviewTarget,
    ResidentVerificationCheck,
)
from ravn.resident_portfolio import LocalResidentWorkItemBackend
from ravn.resident_review import (
    LocalResidentReviewMemory,
    PortfolioArtifactReviewTargetSource,
    ResidentReviewRuntime,
    run_resident_review_wake_pass,
)

MANDATE = (
    "A resident Ravn should create useful artifacts, verify them with concrete evidence, "
    "learn from failures, and only trust reviewed work."
)


def _objective() -> ResidentObjective:
    return ResidentObjective(
        id="artifact-objective",
        title="Create reviewed artifact",
        purpose="Produce and verify a resident artifact.",
        serves_mandate_because="Reviewed work compounds more safely.",
        expected_outcome="Artifact is reviewed and verified.",
        proof_criteria=("Review evidence exists.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        status=ResidentObjectiveStatus.ACTIVE.value,
        artifact_links=("artifact.md",),
    )


async def _seed_backend(tmp_path: Path) -> LocalResidentWorkItemBackend:
    backend = LocalResidentWorkItemBackend(tmp_path / "memory")
    objective = _objective()
    await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE, objectives=(objective,)))
    return backend


def _target(path: Path, *, key_suffix: str | None = None) -> ResidentReviewTarget:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    key = key_suffix or digest
    return ResidentReviewTarget(
        id=f"artifact-{key}",
        title="Reviewed artifact",
        artifact_ref=str(path),
        artifact_kind="markdown",
        source_objective_id="artifact-objective",
        complete_objective_on_pass=True,
        review_key=f"{path}:{key}",
        checks=(
            ResidentVerificationCheck(
                id="requires-proof-line",
                description="Artifact must include a concrete Proof line.",
                command=(
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, sys; "
                        f"text=pathlib.Path({str(path)!r}).read_text(); "
                        "print('proof line present' if 'Proof:' in text else "
                        "'missing proof line'); "
                        "sys.exit(0 if 'Proof:' in text else 1)"
                    ),
                ),
            ),
        ),
        evidence=(str(path),),
    )


async def test_failed_review_creates_actionable_follow_up(tmp_path: Path) -> None:
    backend = await _seed_backend(tmp_path)
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Product Note\n\nUseful draft.\n", encoding="utf-8")
    runtime = ResidentReviewRuntime(
        backend=backend,
        memory=LocalResidentReviewMemory(tmp_path / "memory"),
        verifier=CommandResidentVerificationAdapter(timeout_seconds=5, max_output_bytes=4000),
    )

    report = await runtime.review(MANDATE, _target(artifact))
    objectives = await backend.list_objectives(MANDATE)

    assert report.review.decision == ResidentReviewDecision.FAILED.value
    assert report.created_follow_up_objective_id
    assert any(item.id == report.created_follow_up_objective_id for item in objectives)
    assert any("missing proof line" in item.summary for item in report.review.evidence)


async def test_passing_review_completes_source_objective(tmp_path: Path) -> None:
    backend = await _seed_backend(tmp_path)
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Product Note\n\nProof: command verified this file.\n", encoding="utf-8")
    runtime = ResidentReviewRuntime(
        backend=backend,
        memory=LocalResidentReviewMemory(tmp_path / "memory"),
        verifier=CommandResidentVerificationAdapter(timeout_seconds=5, max_output_bytes=4000),
    )

    report = await runtime.review(MANDATE, _target(artifact))
    source = {item.id: item for item in await backend.list_objectives(MANDATE)}[
        "artifact-objective"
    ]

    assert report.review.decision == ResidentReviewDecision.PASSED.value
    assert source.status == ResidentObjectiveStatus.COMPLETED.value
    assert any("review passed" in item for item in source.proof_progress)


async def test_duplicate_review_key_skips_redundant_verification(tmp_path: Path) -> None:
    backend = await _seed_backend(tmp_path)
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Product Note\n\nProof: command verified this file.\n", encoding="utf-8")
    memory = LocalResidentReviewMemory(tmp_path / "memory")
    runtime = ResidentReviewRuntime(
        backend=backend,
        memory=memory,
        verifier=CommandResidentVerificationAdapter(timeout_seconds=5, max_output_bytes=4000),
    )
    target = _target(artifact, key_suffix="stable")

    first = await runtime.review(MANDATE, target)
    second = await runtime.review(MANDATE, target)

    assert first.review.decision == ResidentReviewDecision.PASSED.value
    assert second.review.decision == ResidentReviewDecision.SKIPPED_DUPLICATE.value
    assert second.duplicate_skipped is True
    reviews = await memory.list_reviews(target.review_key)
    assert len(reviews) == 2


async def test_portfolio_artifact_target_source_uses_configured_checks(
    tmp_path: Path,
) -> None:
    backend = await _seed_backend(tmp_path)
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Product Note\n\nProof: command verified this file.\n", encoding="utf-8")
    source = PortfolioArtifactReviewTargetSource(
        backend=backend,
        max_targets=2,
        rules=[
            {
                "artifact_kind": "markdown",
                "artifact_ref_suffixes": [".md"],
                "complete_objective_on_pass": True,
                "checks": [
                    {
                        "id": "proof-line",
                        "description": "Artifact {artifact_ref} must include proof.",
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "import pathlib, sys; "
                                "text=pathlib.Path({artifact_ref!r}).read_text(); "
                                "sys.exit(0 if 'Proof:' in text else 1)"
                            ),
                        ],
                    }
                ],
            }
        ],
    )
    objective = (await backend.list_objectives(MANDATE))[0]
    await backend.write_objective(
        objective.with_updates(artifact_links=(str(artifact),))
    )

    targets = await source.list_targets(MANDATE)

    assert len(targets) == 1
    assert targets[0].artifact_ref == str(artifact)
    assert targets[0].artifact_kind == "markdown"
    assert targets[0].checks[0].command[0] == sys.executable
    assert str(artifact) in targets[0].checks[0].command[-1]


async def test_review_wake_pass_reviews_portfolio_artifacts_with_real_command(
    tmp_path: Path,
) -> None:
    backend = await _seed_backend(tmp_path)
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Product Note\n\nProof: command verified this file.\n", encoding="utf-8")
    objective = (await backend.list_objectives(MANDATE))[0]
    await backend.write_objective(objective.with_updates(artifact_links=(str(artifact),)))
    memory = LocalResidentReviewMemory(tmp_path / "memory")
    runtime = ResidentReviewRuntime(
        backend=backend,
        memory=memory,
        verifier=CommandResidentVerificationAdapter(timeout_seconds=5, max_output_bytes=4000),
    )
    source = PortfolioArtifactReviewTargetSource(
        backend=backend,
        rules=[
            {
                "artifact_kind": "markdown",
                "artifact_ref_suffixes": [".md"],
                "complete_objective_on_pass": True,
                "checks": [
                    {
                        "id": "proof-line",
                        "description": "Artifact must include a concrete Proof line.",
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "import pathlib, sys; "
                                f"text=pathlib.Path({str(artifact)!r}).read_text(); "
                                "print('proof line present'); "
                                "sys.exit(0 if 'Proof:' in text else 1)"
                            ),
                        ],
                    }
                ],
            }
        ],
    )

    report = await run_resident_review_wake_pass(
        MANDATE,
        runtime=runtime,
        target_source=source,
    )
    source_objective = {item.id: item for item in await backend.list_objectives(MANDATE)}[
        "artifact-objective"
    ]

    assert len(report.targets) == 1
    assert report.reports[0].review.decision == ResidentReviewDecision.PASSED.value
    assert source_objective.status == ResidentObjectiveStatus.COMPLETED.value
    assert any("resident/reviews/" in ref for ref in report.persisted_refs)
