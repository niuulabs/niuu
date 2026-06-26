#!/usr/bin/env python
"""Run a bounded multi-pass resident portfolio stewardship proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioStewardActionKind,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentRun,
)
from ravn.adapters.resident_work.mimir import MimirResidentWorkAdapter
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    ResidentPortfolioStewardConfig,
    ResidentPortfolioStewardRuntime,
    render_steward_report,
)

AUTONOMY_MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts.\n"
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class ProofWakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, mandate: str) -> WakefulResidentRun:
        self.calls.append(mandate)
        cycle = WakefulResidentCycleRecord(
            cycle_number=1,
            mandate=mandate,
            prior_domain_model_ref="resident/domain-expert/domain-model.md",
            attention_reason="portfolio steward selected bounded work",
            selected_action="advance selected resident objective",
            work_created_or_advanced=("resident objective advanced with proof",),
            artifact_refs=("resident/domain-expert/artifacts/steward-proof.md",),
            finding_summaries=(
                "missing capability: generic resident evidence reviewer",
                "opportunity: compare follow-up work before the next advance",
            ),
            decision=WakefulResidentDecisionKind.STOP,
            decision_reason="max turns reached: 1",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=17, output_tokens=19),
            ),
        )
        return WakefulResidentRun(
            mandate=mandate,
            cycles=(cycle,),
            final_decision=WakefulResidentDecisionKind.STOP,
            final_reason=cycle.decision_reason,
            budget=cycle.budget,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="", help="Path to ravn config YAML")
    parser.add_argument("--workspace", default="", help="Workspace root for proof artifacts")
    parser.add_argument("--mandate", default=AUTONOMY_MANDATE)
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--max-advancements", type=int, default=1)
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
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    kind: str = ResidentObjectiveKind.RESEARCH.value,
    source_evidence: tuple[str, ...] = ("seeded proof evidence",),
    reasoning: str = "seeded proof reason",
    proof_progress: tuple[str, ...] = (),
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident autonomy mandate.",
        expected_outcome="A durable proof artifact exists.",
        proof_criteria=("A durable proof artifact exists.",),
        kind=kind,
        status=status,
        source_evidence=source_evidence,
        reasoning=reasoning,
        proof_progress=proof_progress,
    )


async def _seed_portfolio(backend: Any, mandate: str) -> None:
    paused_needing_repair = _objective(
        "paused-steward-objective",
        "Paused steward objective",
        status=ResidentObjectiveStatus.PAUSED.value,
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        source_evidence=("prior wake found this objective still matters",),
        reasoning="",
    )
    completed_without_proof = _objective(
        "completed-without-proof",
        "Completed objective without proof",
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=(),
    )
    later_candidate = _objective(
        "later-candidate",
        "Later candidate objective",
        kind=ResidentObjectiveKind.RESEARCH.value,
    )
    for objective in (paused_needing_repair, completed_without_proof, later_candidate):
        await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=(paused_needing_repair, completed_without_proof, later_candidate),
            decision_history=("seeded resident stewardship proof portfolio",),
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
        backend: Any = MimirResidentWorkAdapter(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate)

    wake_runtime = ProofWakeRuntime()
    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=wake_runtime,
        config=ResidentPortfolioStewardConfig(
            max_passes=args.max_passes,
            max_advancements=args.max_advancements,
            max_follow_up_objectives=2,
        ),
    ).run(mandate)

    print("[proof] Resident portfolio steward proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] passes={len(run.passes)}")
    print(f"[proof] wake_calls={len(wake_runtime.calls)}")
    print(f"[proof] final_action={run.final_action.value}")
    print(f"[proof] final_suggested_next_action={run.final_suggested_next_action}")
    for report in run.passes:
        print(
            "[proof] pass="
            f"{report.pass_number} action={report.action_taken.value} "
            f"before={report.validation_before.verdict} after={report.validation_after.verdict} "
            f"repairs={len(report.repairs_attempted)} skipped={len(report.repairs_skipped)} "
            f"followups={len(report.new_follow_up_objectives)}"
        )
    print()
    print(render_steward_report(run))

    if len(run.passes) > args.max_passes:
        raise SystemExit("[proof] steward exceeded configured pass bound")
    if not seeded:
        return
    if len(run.passes) < 3:
        raise SystemExit("[proof] expected seeded proof to show three sequential passes")
    if run.passes[0].action_taken != ResidentPortfolioStewardActionKind.REPAIR:
        raise SystemExit("[proof] expected first pass to repair safe drift")
    if not run.passes[0].repairs_attempted:
        raise SystemExit("[proof] expected at least one safe repair")
    if not run.passes[0].repairs_skipped:
        raise SystemExit("[proof] expected at least one explicit skipped repair")
    if run.passes[1].action_taken != ResidentPortfolioStewardActionKind.ADVANCE:
        raise SystemExit("[proof] expected second pass to advance selected work")
    if not run.passes[1].new_follow_up_objectives:
        raise SystemExit("[proof] expected follow-up objectives from wake evidence")
    if run.passes[2].action_taken != ResidentPortfolioStewardActionKind.SLEEP:
        raise SystemExit("[proof] expected third pass to stop/sleep by advancement bound")
    if not wake_runtime.calls:
        raise SystemExit("[proof] expected wake runtime to be invoked once")


if __name__ == "__main__":
    asyncio.run(_main())
