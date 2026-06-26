#!/usr/bin/env python
"""Run a read-only resident portfolio validation and dry-run selection proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.adapters.resident_work.mimir import MimirResidentWorkAdapter
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    ResidentPortfolioValidator,
    render_validation_report,
)

AUTONOMY_MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts.\n"
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="", help="Path to ravn config YAML")
    parser.add_argument("--workspace", default="", help="Workspace root for proof artifacts")
    parser.add_argument("--mandate", default=AUTONOMY_MANDATE)
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Validate the existing configured portfolio instead of seeding proof data.",
    )
    parser.add_argument("--max-selected", type=int, default=1)
    parser.add_argument("--max-active", type=int, default=3)
    return parser.parse_args()


async def _seed_portfolio(backend: Any, mandate: str) -> None:
    completed_expert = ResidentObjective(
        id="resident-domain-expert-loop-v0",
        title="Resident Domain Expert Loop V0",
        purpose="Completed resident domain expert milestone.",
        serves_mandate_because="It lets residents infer useful work from a mandate.",
        expected_outcome="Completed proof exists.",
        proof_criteria=("Domain expert proof passed.",),
        kind=ResidentObjectiveKind.IMPLEMENTATION.value,
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=("Proof passed.",),
    )
    completed_wakeful = ResidentObjective(
        id="wakeful-resident-runtime-v0",
        title="Wakeful Resident Runtime V0",
        purpose="Completed wakeful runtime milestone.",
        serves_mandate_because="It lets residents wake, act, persist, and stop.",
        expected_outcome="Completed proof exists.",
        proof_criteria=("Wakeful proof passed.",),
        dependencies=("resident-domain-expert-loop-v0",),
        kind=ResidentObjectiveKind.IMPLEMENTATION.value,
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=("Proof passed.",),
    )
    completed_portfolio_without_proof = ResidentObjective(
        id="long-horizon-work-management-v0",
        title="Resident Long-Horizon Work Management V0",
        purpose="Completed portfolio manager milestone.",
        serves_mandate_because="It lets residents manage long-horizon work.",
        expected_outcome="Completed proof exists.",
        proof_criteria=("Portfolio proof passed.",),
        dependencies=("wakeful-resident-runtime-v0",),
        kind=ResidentObjectiveKind.IMPLEMENTATION.value,
        status=ResidentObjectiveStatus.COMPLETED.value,
    )
    ready = ResidentObjective(
        id="portfolio-validator-dry-run-selector-v0",
        title="Portfolio Validator and Dry-Run Selector V0",
        purpose="Make resident portfolio records inspectable before advancement.",
        serves_mandate_because="It prevents the resident from scaling incoherent work state.",
        expected_outcome="Validation report and selected next objective preview exist.",
        proof_criteria=("Dry-run report selects one next objective without mutation.",),
        dependencies=("long-horizon-work-management-v0",),
        kind=ResidentObjectiveKind.VERIFICATION.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        source_evidence=("Portfolio records now exist but need read-only validation.",),
        reasoning="The next safe step is validating resident work state before more autonomy.",
    )
    blocked = ResidentObjective(
        id="multi-objective-concurrency-v0",
        title="Multi-objective Concurrency V0",
        purpose="Manage several active resident objectives at once.",
        serves_mandate_because="The vision needs many parallel workstreams.",
        expected_outcome="Concurrency policy and proof exist.",
        proof_criteria=("Concurrent objective proof passed.",),
        dependencies=("missing-remote-orchestration-v0",),
        kind=ResidentObjectiveKind.REMOTE_EXECUTION.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        source_evidence=("Concurrency should wait until validation exists.",),
        reasoning="This is important but dependency readiness is not proven.",
    )
    operator_needed = ResidentObjective(
        id="choose-operator-cadence",
        title="Choose Operator Cadence",
        purpose="Ask how often the resident should summarize or request approval.",
        serves_mandate_because="Operator relationship should improve without babysitting.",
        expected_outcome="Operator cadence preference is recorded.",
        proof_criteria=("Operator gives a cadence preference.",),
        kind=ResidentObjectiveKind.OPERATOR_QUESTION.value,
        status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
    )

    for objective in (
        completed_expert,
        completed_wakeful,
        completed_portfolio_without_proof,
        ready,
        blocked,
        operator_needed,
    ):
        await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=(
                completed_expert,
                completed_wakeful,
                completed_portfolio_without_proof,
                ready,
                blocked,
                operator_needed,
            ),
            wake_record_links=("resident/wakeful/cycles/20260620T000000Z-1.md",),
            artifact_links=("resident/domain-expert/artifacts/proof.md",),
            consolidation_links=(
                "resident/domain-expert/consolidations/20260620T000000Z-proof.md",
            ),
            decision_history=("seeded validation proof portfolio",),
        )
    )


async def _snapshot(backend: Any, *, mimir: Any | None, local_root: Path | None) -> dict[str, str]:
    refs = await backend.list_refs("resident/portfolio")
    data: dict[str, str] = {}
    for ref in refs:
        if mimir is not None:
            data[ref] = await mimir.read_page(ref)
        elif local_root is not None:
            data[ref] = (local_root / ref).read_text(encoding="utf-8")
    return data


async def _main() -> None:
    args = _parse_args()
    if args.config:
        os.environ["RAVN_CONFIG"] = args.config
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        os.chdir(workspace)

    settings = Settings()
    _configure_logging(settings)
    mimir = _build_mimir(settings)
    if mimir is not None:
        backend: Any = MimirResidentWorkAdapter(mimir)
        local_root = None
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        memory_label = str(local_root)

    mandate = str(args.mandate).strip() or AUTONOMY_MANDATE
    seed_proof = not args.no_seed
    if seed_proof:
        await _seed_portfolio(backend, mandate)
    before = await _snapshot(backend, mimir=mimir, local_root=local_root)
    report = await ResidentPortfolioValidator(
        backend=backend,
        max_selected=args.max_selected,
        max_active=args.max_active,
    ).validate(mandate)
    after = await _snapshot(backend, mimir=mimir, local_root=local_root)
    rendered = render_validation_report(report)

    print("[proof] Resident portfolio validator proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seed_proof}")
    print(f"[proof] verdict={report.verdict}")
    print(f"[proof] issues={len(report.issues)}")
    print(f"[proof] warnings={len(report.warnings)}")
    print(f"[proof] mutated_state={before != after}")
    if report.selected_objective is not None:
        print(f"[proof] selected={report.selected_objective.objective_id}")
        print(f"[proof] selected_reason={report.selected_objective.priority_rationale}")
    print()
    print(rendered)

    if not seed_proof:
        return

    if not report.issues:
        raise SystemExit("[proof] expected seeded portfolio issues to be detected")
    if report.selected_objective is None:
        raise SystemExit("[proof] expected dry-run selector to choose a next objective")
    if not report.selected_objective.priority_rationale:
        raise SystemExit("[proof] expected selection rationale")
    if before != after or report.mutated_state:
        raise SystemExit("[proof] validator mutated portfolio state")
    if not any(issue.code == "completed_without_proof" for issue in report.issues):
        raise SystemExit("[proof] expected completed-without-proof issue")
    if not any(issue.code == "missing_dependency" for issue in report.issues):
        raise SystemExit("[proof] expected missing dependency issue")
    if not any(issue.code == "operator_question_missing" for issue in report.issues):
        raise SystemExit("[proof] expected missing operator question issue")
    if not any("dependencies missing" in reason for reason in report.skipped_reasons):
        raise SystemExit("[proof] expected dependency skip reason")
    if not any("operator input needed" in reason for reason in report.skipped_reasons):
        raise SystemExit("[proof] expected operator-needed skip reason")


if __name__ == "__main__":
    asyncio.run(_main())
