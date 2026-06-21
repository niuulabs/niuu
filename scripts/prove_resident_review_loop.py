#!/usr/bin/env python3
"""Prove resident self-review with real verification and portfolio updates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

from ravn.adapters.review.command import CommandResidentVerificationAdapter
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.domain.resident_review import ResidentReviewTarget, ResidentVerificationCheck
from ravn.resident_portfolio import LocalResidentWorkItemBackend
from ravn.resident_review import LocalResidentReviewMemory, ResidentReviewRuntime

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company.\n"
    "You are its resident Ravn.\n"
    "Help it become easier to run, more creative, and more successful.\n"
    "Ask before spending money or operating physical machines."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="", help="Proof workspace directory.")
    parser.add_argument("--mandate", default=MANDATE, help="Resident mandate.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace or Path.cwd() / ".resident-review-proof").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)

    memory_root = workspace / ".ravn"
    backend = LocalResidentWorkItemBackend(memory_root)
    review_memory = LocalResidentReviewMemory(memory_root)
    runtime = ResidentReviewRuntime(
        backend=backend,
        memory=review_memory,
        verifier=CommandResidentVerificationAdapter(timeout_seconds=10, max_output_bytes=8000),
    )
    await _seed_portfolio(backend, args.mandate)

    artifact = workspace / "resident-product-note.md"
    artifact.write_text(
        "# Product Note\n\n"
        "A compact product-listing note for a terrain model.\n",
        encoding="utf-8",
    )
    failed = await runtime.review(args.mandate, _target(artifact, suffix="bad"))

    artifact.write_text(
        "# Product Note\n\n"
        "A compact product-listing note for a terrain model.\n\n"
        "Proof: command verification confirmed this artifact includes evidence.\n",
        encoding="utf-8",
    )
    passed_target = _target(artifact)
    passed = await runtime.review(args.mandate, passed_target)
    duplicate = await runtime.review(args.mandate, passed_target)

    objectives = await backend.list_objectives(args.mandate)
    objective_by_id = {item.id: item for item in objectives}
    source = objective_by_id["resident-product-note"]
    reviews = await review_memory.list_reviews()

    if failed.review.decision != "failed":
        raise SystemExit("[proof] expected first review to fail")
    if not failed.created_follow_up_objective_id:
        raise SystemExit("[proof] expected failed review to create follow-up work")
    if failed.created_follow_up_objective_id not in objective_by_id:
        raise SystemExit("[proof] expected follow-up objective to persist")
    if passed.review.decision != "passed":
        raise SystemExit("[proof] expected corrected artifact to pass")
    if source.status != ResidentObjectiveStatus.COMPLETED.value:
        raise SystemExit("[proof] expected reviewed source objective to complete")
    if duplicate.review.decision != "skipped_duplicate" or not duplicate.duplicate_skipped:
        raise SystemExit("[proof] expected duplicate review to be skipped")
    if not any(review.decision == "failed" for review in reviews):
        raise SystemExit("[proof] expected failed review artifact to persist")
    if not any(review.decision == "passed" for review in reviews):
        raise SystemExit("[proof] expected passed review artifact to persist")

    duplicate_audit = await review_memory.write_audit(
        "# Duplicate Review Path Audit\n\n"
        "- existing human/operator review path: ODIN review queue\n"
        "- existing delegated-result review: ResidentDelegationRuntime review artifacts\n"
        "- new path: ResidentReviewRuntime for concrete machine verification of artifacts\n"
        "- duplicate behavior: exact review_key rerun is skipped unless configured otherwise\n"
        f"- duplicate_review_id: {duplicate.review.id}\n"
    )

    print("[proof] Resident self-review proof.")
    print(f"[proof] workspace={workspace}")
    print(f"[proof] memory={memory_root}")
    print(f"[proof] failed_decision={failed.review.decision}")
    print(f"[proof] failed_follow_up={failed.created_follow_up_objective_id}")
    print(f"[proof] passed_decision={passed.review.decision}")
    print(f"[proof] source_objective_status={source.status}")
    print(f"[proof] duplicate_decision={duplicate.review.decision}")
    print(f"[proof] duplicate_skipped={duplicate.duplicate_skipped}")
    print(f"[proof] duplicate_audit={duplicate_audit}")
    print(f"[proof] reviews={len(reviews)}")
    for ref in (*failed.persisted_refs, *passed.persisted_refs, *duplicate.persisted_refs):
        print(f"[proof] ref={ref}")


async def _seed_portfolio(backend: LocalResidentWorkItemBackend, mandate: str) -> None:
    objective = ResidentObjective(
        id="resident-product-note",
        title="Create reviewed product note",
        purpose="Produce a product note that has concrete review evidence.",
        serves_mandate_because="The resident should improve the company with trusted artifacts.",
        expected_outcome="A reviewed product note exists.",
        proof_criteria=("A concrete review artifact verifies the product note.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        status=ResidentObjectiveStatus.ACTIVE.value,
        artifact_links=("resident-product-note.md",),
        reasoning="Resident-authored artifacts must pass review before completion.",
    )
    await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=mandate, objectives=(objective,)))


def _target(path: Path, *, suffix: str = "") -> ResidentReviewTarget:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    key = suffix or digest
    return ResidentReviewTarget(
        id=f"product-note-{key}",
        title="Resident product note",
        artifact_ref=str(path),
        artifact_kind="markdown",
        source_objective_id="resident-product-note",
        review_key=f"{path}:{key}",
        complete_objective_on_pass=True,
        checks=(
            ResidentVerificationCheck(
                id="proof-line-present",
                description="The artifact must include a Proof line.",
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


if __name__ == "__main__":
    asyncio.run(_main())

