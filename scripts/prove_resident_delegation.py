#!/usr/bin/env python
"""Run a bounded resident delegation orchestration proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.resident_portfolio import (
    ResidentDelegationStatus,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    LocalSimulatedResidentExecutor,
    MimirResidentWorkItemBackend,
    ResidentDelegationConfig,
    ResidentDelegationRuntime,
    render_delegation_result,
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
        help="Run against the existing configured portfolio instead of seeding proof data.",
    )
    return parser.parse_args()


def _objective(
    objective_id: str,
    title: str,
    *,
    risk_boundaries: tuple[str, ...] = (),
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance generic delegated work around {title}.",
        serves_mandate_because="It lets the resident advance bounded work without babysitting.",
        expected_outcome="A delegated worker result is persisted and reviewed.",
        proof_criteria=("Delegation record and result artifact are persisted.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        risk_boundaries=risk_boundaries,
        source_evidence=(f"ready delegated objective: {title}",),
        reasoning="This objective is ready for bounded delegated execution.",
    )


async def _seed_portfolio(backend: Any, mandate: str) -> None:
    objectives = (
        _objective("generic-research-one", "Review generic evidence path"),
        _objective("generic-research-two", "Draft generic execution notes"),
        _objective(
            "generic-risky-work",
            "Operate bounded external path",
            risk_boundaries=("external_side_effect",),
        ),
    )
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=objectives,
            decision_history=("seeded resident delegation proof portfolio",),
        )
    )


async def _main() -> None:
    args = _parse_args()
    if args.config:
        os.environ["RAVN_CONFIG"] = args.config
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        os.chdir(workspace)

    mandate = str(args.mandate).strip() or AUTONOMY_MANDATE
    settings = Settings()
    _configure_logging(settings)
    mimir = _build_mimir(settings)
    if mimir is not None:
        backend: Any = MimirResidentWorkItemBackend(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate)

    executor = LocalSimulatedResidentExecutor()
    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=2, max_observations=4),
    ).run(mandate)
    after_objectives = await backend.list_objectives(mandate)
    delegations = await backend.list_delegations(mandate)

    print("[proof] Resident delegation orchestration proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] selected={len(report.selected_objectives)}")
    print(f"[proof] launched={len(report.created_delegations)}")
    print(f"[proof] observed_results={len(report.observed_results)}")
    print(f"[proof] gated={len(report.skipped_or_gated_objectives)}")
    print(f"[proof] operator_questions={len(report.operator_questions)}")
    print(f"[proof] follow_ups={len(report.created_follow_up_objectives)}")
    print(f"[proof] persisted_refs={len(report.persisted_refs)}")
    print(f"[proof] objectives_after={len(after_objectives)}")
    print(f"[proof] delegations_after={len(delegations)}")
    print(f"[proof] final_suggested_next_action={report.final_suggested_next_action}")
    for delegation in report.created_delegations:
        print(
            "[proof] delegation="
            f"{delegation.id} session={delegation.backend_session_id} status={delegation.status}"
        )
    for question in report.operator_questions:
        print(f"[proof] operator_question={question}")
    if report.created_delegations and report.observed_results:
        print()
        print(render_delegation_result(report.created_delegations[0], report.observed_results[0]))

    if not seeded:
        return
    if len(report.created_delegations) < 2:
        raise SystemExit("[proof] expected at least two created delegations")
    if len(executor.launched) < 2:
        raise SystemExit("[proof] expected executor port to launch at least two sessions")
    if not report.observed_results:
        raise SystemExit("[proof] expected observed delegated results")
    if not delegations:
        raise SystemExit("[proof] expected persisted delegation records")
    if not any(ref.startswith("resident/delegations/") for ref in report.persisted_refs):
        raise SystemExit("[proof] expected persisted delegation record ref")
    if not any(ref.startswith("resident/delegation-results/") for ref in report.persisted_refs):
        raise SystemExit("[proof] expected persisted delegation result ref")
    if not any(
        objective.status == ResidentObjectiveStatus.COMPLETED.value
        for objective in after_objectives
        if objective.id in {"generic-research-one", "generic-research-two"}
    ):
        raise SystemExit("[proof] expected delegated source objective to be completed")
    if not any(
        objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
        for objective in after_objectives
    ):
        raise SystemExit("[proof] expected risky delegation to route to operator")
    if not any(
        objective.title.startswith("Follow up delegated result") for objective in after_objectives
    ):
        raise SystemExit("[proof] expected follow-up objective from delegated result")
    if not any(
        delegation.status == ResidentDelegationStatus.COMPLETED.value and delegation.result_refs
        for delegation in delegations
    ):
        raise SystemExit("[proof] expected completed delegation record with result ref")


if __name__ == "__main__":
    asyncio.run(_main())
