#!/usr/bin/env python
"""Run a bounded wakeful portfolio steward integration proof."""

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
)
from ravn.domain.wakeful_resident import (
    WakefulPortfolioStewardActionKind,
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentRun,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    MimirResidentWorkItemBackend,
    ResidentPortfolioStewardConfig,
    ResidentPortfolioStewardRuntime,
)
from ravn.wakeful_resident import (
    LocalWakefulPortfolioStewardMemory,
    MimirWakefulPortfolioStewardMemory,
    WakefulPortfolioStewardConfig,
    WakefulPortfolioStewardRuntime,
    render_wakeful_portfolio_steward_report,
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
            attention_reason="wakeful portfolio integration selected bounded work",
            selected_action="advance selected portfolio objective",
            work_created_or_advanced=("portfolio objective advanced from wake attention",),
            artifact_refs=("resident/domain-expert/artifacts/wakeful-portfolio-proof.md",),
            finding_summaries=(
                "missing capability: generic wakeful steward evidence review",
                "opportunity: compare resident follow-up work before advancing again",
            ),
            decision=WakefulResidentDecisionKind.STOP,
            decision_reason="max turns reached: 1",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=23, output_tokens=29),
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
    parser.add_argument("--max-wake-passes", type=int, default=2)
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
        "paused-wakeful-steward-objective",
        "Paused wakeful steward objective",
        status=ResidentObjectiveStatus.PAUSED.value,
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        source_evidence=("prior wake found this portfolio objective still matters",),
        reasoning="",
    )
    completed_without_proof = _objective(
        "completed-without-proof",
        "Completed objective without proof",
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=(),
    )
    later_candidate = _objective(
        "later-wakeful-candidate",
        "Later wakeful candidate objective",
        kind=ResidentObjectiveKind.RESEARCH.value,
    )
    for objective in (paused_needing_repair, completed_without_proof, later_candidate):
        await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=(paused_needing_repair, completed_without_proof, later_candidate),
            decision_history=("seeded wakeful portfolio steward proof portfolio",),
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
        memory: Any = MimirWakefulPortfolioStewardMemory(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        memory = LocalWakefulPortfolioStewardMemory(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate)

    proof_wake = ProofWakeRuntime()
    steward = ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=proof_wake,
        config=ResidentPortfolioStewardConfig(
            max_passes=1,
            max_advancements=1,
            max_follow_up_objectives=2,
        ),
    )
    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=args.max_wake_passes),
    ).run(mandate)

    records = await memory.list_records(mandate, limit=10)
    print("[proof] Wakeful portfolio steward integration proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] wake_passes={len(run.records)}")
    print(f"[proof] persisted_records={len(records)}")
    print(f"[proof] steward_wake_calls={len(proof_wake.calls)}")
    print(f"[proof] final_action={run.final_action.value}")
    print(f"[proof] final_reason={run.final_reason}")
    for record in run.records:
        print(
            "[proof] wake="
            f"{record.wake_number} action={record.action_taken.value} "
            f"attention={record.attention_reason!r} "
            f"steward_passes={record.steward_pass_count} "
            f"steward_final={record.steward_final_action or 'none'}"
        )
    print()
    print(render_wakeful_portfolio_steward_report(run))

    if len(run.records) > args.max_wake_passes:
        raise SystemExit("[proof] wakeful integration exceeded configured wake bound")
    if not seeded:
        return
    if len(run.records) < 2:
        raise SystemExit("[proof] expected at least two wake passes")
    if not records:
        raise SystemExit("[proof] expected persisted integration records")
    if not any(
        "portfolio validation issue needs stewardship" in record.attention_reason
        for record in run.records
    ):
        raise SystemExit("[proof] expected attention derived from portfolio state")
    if not any(
        record.action_taken == WakefulPortfolioStewardActionKind.RUN_STEWARD
        for record in run.records
    ):
        raise SystemExit("[proof] expected steward to be invoked from wakefulness")
    if not any("repairs=1" in item for record in run.records for item in record.steward_summary):
        raise SystemExit("[proof] expected a repair in steward summary")
    if not any("skipped=1" in item for record in run.records for item in record.steward_summary):
        raise SystemExit("[proof] expected an explicit skipped repair")
    if not any("advance" in item for record in run.records for item in record.steward_summary):
        raise SystemExit("[proof] expected an advancement in steward summary")
    if len(proof_wake.calls) != 1:
        raise SystemExit("[proof] expected exactly one bounded wake runtime advancement")
    if run.final_action not in {
        WakefulPortfolioStewardActionKind.SLEEP,
        WakefulPortfolioStewardActionKind.STOP,
    }:
        raise SystemExit("[proof] expected final sleep/stop due to bounds or no work")


if __name__ == "__main__":
    asyncio.run(_main())
