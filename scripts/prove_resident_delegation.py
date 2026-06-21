#!/usr/bin/env python
"""Run a bounded resident delegation orchestration proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs

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
    parser.add_argument(
        "--require-real-session",
        action="store_true",
        help="Fail unless at least one non-local delegated backend session is launched.",
    )
    parser.add_argument(
        "--allow-local-backend",
        action="store_true",
        help="Allow local-subprocess/local-simulated backends for development only.",
    )
    parser.add_argument(
        "--poll-attempts",
        type=int,
        default=1,
        help="Number of resident delegation runtime passes to observe real workers.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=10.0,
        help="Delay between polling passes when waiting for real workers.",
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


def _build_executor(settings: Settings) -> Any:
    cfg = settings.resident_delegation_execution
    cls = import_class(cfg.adapter)
    kwargs = resolve_secret_kwargs(dict(cfg.kwargs), dict(cfg.secret_kwargs_env))
    return cls(**kwargs)


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

    executor = _build_executor(settings)
    delegation_cfg = settings.resident_delegation_execution
    runtime = ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(
            max_delegations=max(1, int(delegation_cfg.max_delegations)),
            max_observations=max(1, int(delegation_cfg.max_observations)),
            max_follow_up_objectives=max(0, int(delegation_cfg.max_follow_up_objectives)),
            approved_risk_objective_ids=tuple(delegation_cfg.approved_risk_objective_ids),
        ),
    )
    report = await runtime.run(mandate)
    created_delegations = list(report.created_delegations)
    observed_results = list(report.observed_results)
    gated_objectives = list(report.skipped_or_gated_objectives)
    operator_questions = list(report.operator_questions)
    follow_up_objectives = list(report.created_follow_up_objectives)
    persisted_refs = list(report.persisted_refs)
    attempts = max(1, int(args.poll_attempts))
    for attempt in range(1, attempts):
        if observed_results:
            break
        await asyncio.sleep(max(0.0, float(args.poll_interval_seconds)))
        report = await runtime.run(mandate)
        created_delegations.extend(report.created_delegations)
        observed_results.extend(report.observed_results)
        gated_objectives.extend(report.skipped_or_gated_objectives)
        operator_questions.extend(report.operator_questions)
        follow_up_objectives.extend(report.created_follow_up_objectives)
        persisted_refs.extend(report.persisted_refs)
        print(
            "[proof] poll="
            f"{attempt + 1}/{attempts} launched={len(report.created_delegations)} "
            f"observed_results={len(report.observed_results)}"
        )
    after_objectives = await backend.list_objectives(mandate)
    delegations = await backend.list_delegations(mandate)
    delegation_evidence = created_delegations or list(delegations)

    print("[proof] Resident delegation orchestration proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] executor_adapter={delegation_cfg.adapter}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] selected={len(report.selected_objectives)}")
    print(f"[proof] launched={len(created_delegations)}")
    print(f"[proof] observed_results={len(observed_results)}")
    print(f"[proof] gated={len(gated_objectives)}")
    print(f"[proof] operator_questions={len(operator_questions)}")
    print(f"[proof] follow_ups={len(follow_up_objectives)}")
    print(f"[proof] persisted_refs={len(persisted_refs)}")
    print(f"[proof] objectives_after={len(after_objectives)}")
    print(f"[proof] delegations_after={len(delegations)}")
    print(f"[proof] final_suggested_next_action={report.final_suggested_next_action}")
    for delegation in delegation_evidence:
        print(
            "[proof] delegation="
            f"{delegation.id} session={delegation.backend_session_id} status={delegation.status}"
        )
    for question in operator_questions:
        print(f"[proof] operator_question={question}")
    if delegation_evidence and observed_results:
        print()
        print(render_delegation_result(delegation_evidence[0], observed_results[0]))

    if not seeded:
        return
    if len(delegation_evidence) < 1:
        raise SystemExit("[proof] expected at least one persisted delegation")
    local_backends = {"local-simulated", "local-subprocess"}
    if not args.allow_local_backend and any(
        delegation.backend_name in local_backends for delegation in delegation_evidence
    ):
        raise SystemExit("[proof] local delegation backends do not count as real proof")
    if args.require_real_session and not any(
        delegation.backend_name not in local_backends and delegation.backend_session_id
        for delegation in delegation_evidence
    ):
        raise SystemExit("[proof] expected at least one real delegated worker session")
    if not observed_results:
        raise SystemExit("[proof] expected observed delegated results")
    if not delegations:
        raise SystemExit("[proof] expected persisted delegation records")
    if not any(ref.startswith("resident/delegations/") for ref in persisted_refs):
        raise SystemExit("[proof] expected persisted delegation record ref")
    if not any(ref.startswith("resident/delegation-results/") for ref in persisted_refs):
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
