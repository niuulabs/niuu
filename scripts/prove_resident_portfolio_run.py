#!/usr/bin/env python
"""Run a real resident long-horizon portfolio proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from ravn.cli.commands import (
    _build_agent,
    _build_mimir,
    _configure_logging,
    _resolve_persona,
)
from ravn.config import ProjectConfig, Settings
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
)
from ravn.resident_continuation import LocalResidentMemory, MimirResidentMemory
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    MimirResidentDomainExpertMemory,
    ResidentDomainExpertConfig,
    ResidentDomainExpertLoop,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    MimirResidentWorkItemBackend,
    ResidentLongHorizonWorkManager,
    ResidentPortfolioConfig,
)
from ravn.wakeful_resident import (
    LocalWakefulResidentMemory,
    MimirWakefulResidentMemory,
    WakefulResidentConfig,
    WakefulResidentRuntime,
)

AUTONOMY_MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts.\n"
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="", help="Path to ravn config YAML")
    parser.add_argument("--persona", default="domain-drive", help="Resident persona name")
    parser.add_argument("--workspace", default="", help="Workspace root for proof artifacts")
    parser.add_argument("--max-objectives-selected", type=int, default=1)
    parser.add_argument("--max-active-objectives", type=int, default=3)
    parser.add_argument("--max-wake-cycles", type=int, default=1)
    parser.add_argument("--orientation-turns", type=int, default=1)
    parser.add_argument("--max-active-workstreams", type=int, default=1)
    parser.add_argument("--max-workstream-turns", type=int, default=1)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=1800.0)
    parser.add_argument("--max-tokens", type=int, default=0)
    return parser.parse_args()


async def _deny_by_default(question: str) -> str:
    print(f"\n[resident asks]\n{question}\n")
    print("[proof] No operator approval granted in proof mode; recording the question.")
    return "No approval granted during proof mode; ask again in an operator-supervised run."


async def _list_refs(
    *,
    backend: Any,
    prefix: str,
) -> list[str]:
    return await backend.list_refs(prefix)


async def _seed_autonomy_memory(
    *,
    expert_memory: Any,
    wake_memory: Any,
    work_backend: Any,
) -> None:
    await expert_memory.write_domain_model(
        ResidentDomainModel(
            mandate=AUTONOMY_MANDATE,
            current_understanding=(
                "Resident Domain Expert Loop V0 and Wakeful Resident Runtime V0 are complete. "
                "The next missing layer is durable long-horizon work ownership."
            ),
            known_facts=(
                "Resident Domain Expert Loop V0 is complete.",
                "Wakeful Resident Runtime V0 is complete.",
            ),
            hypotheses=(
                "The resident needs a portfolio of objectives rather than a single workstream.",
                "Prioritization, dependencies, proof, and review should be durable work state.",
            ),
            open_questions=(
                "Which resident objective should be advanced next without operator prompting?",
            ),
            opportunities=(
                "Use completed resident milestones to select the next autonomy capability.",
            ),
            capability_gaps=(
                "No long-horizon portfolio backend exists for resident work items.",
                "Prioritization across competing resident objectives is missing.",
                "Objective dependency, cancellation, review, and resumption are not durable yet.",
            ),
            recent_outcomes=(
                "Resident Domain Expert Loop V0 proof passed.",
                "Wakeful Resident Runtime V0 proof passed.",
            ),
        )
    )
    await work_backend.write_objective(
        ResidentObjective(
            id="resident-domain-expert-loop-v0",
            title="Resident Domain Expert Loop V0",
            purpose="Completed resident domain-expert loop milestone.",
            serves_mandate_because="It lets residents infer and advance useful domain work.",
            expected_outcome="Completed proof exists.",
            proof_criteria=("Domain expert proof passed.",),
            kind=ResidentObjectiveKind.IMPLEMENTATION.value,
            status=ResidentObjectiveStatus.COMPLETED.value,
            proof_progress=("Proof passed with KVM mandate.",),
        )
    )
    await work_backend.write_objective(
        ResidentObjective(
            id="wakeful-resident-runtime-v0",
            title="Wakeful Resident Runtime V0",
            purpose="Completed resident wake cycle milestone.",
            serves_mandate_because="It lets residents wake, act, persist state, and stop.",
            expected_outcome="Completed proof exists.",
            proof_criteria=("Wakeful proof passed.",),
            kind=ResidentObjectiveKind.IMPLEMENTATION.value,
            dependencies=("resident-domain-expert-loop-v0",),
            status=ResidentObjectiveStatus.COMPLETED.value,
            proof_progress=("Proof passed with three wake cycles.",),
        )
    )
    await wake_memory.write_wake_record(
        WakefulResidentCycleRecord(
            cycle_number=1,
            mandate=AUTONOMY_MANDATE,
            prior_domain_model_ref="resident/domain-expert/domain-model.md",
            attention_reason="wakeful runtime proof completed",
            selected_action="record completed resident milestone",
            work_created_or_advanced=("wakeful-resident-runtime-v0: completed",),
            artifact_refs=("resident/domain-expert/artifacts/wakeful-proof.md",),
            finding_summaries=("Wakeful runtime V0 completed and needs portfolio ownership next.",),
            decision=WakefulResidentDecisionKind.STOP,
            decision_reason="max turns reached: 3",
            budget=ResidentBudgetSnapshot(turns_used=3),
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

    settings = Settings()
    _configure_logging(settings)
    project_config = ProjectConfig.discover()
    persona = _resolve_persona(args.persona, project_config, settings=settings, cwd=Path.cwd())
    agent, _channel = _build_agent(settings, persona_config=persona)

    mimir = _build_mimir(settings)
    if mimir is not None:
        continuation_memory: Any = MimirResidentMemory(mimir)
        expert_memory: Any = MimirResidentDomainExpertMemory(mimir)
        wake_memory: Any = MimirWakefulResidentMemory(mimir)
        work_backend: Any = MimirResidentWorkItemBackend(mimir)
        memory_label = "mimir"
    else:
        root = Path.cwd() / ".ravn"
        continuation_memory = LocalResidentMemory(root)
        expert_memory = LocalResidentDomainExpertMemory(root)
        wake_memory = LocalWakefulResidentMemory(root)
        work_backend = LocalResidentWorkItemBackend(root)
        memory_label = str(root)

    await _seed_autonomy_memory(
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        work_backend=work_backend,
    )

    expert_loop = ResidentDomainExpertLoop(
        agent=agent,
        persona_config=persona,
        continuation_memory=continuation_memory,
        expert_memory=expert_memory,
        config=ResidentDomainExpertConfig(
            orientation_turns=args.orientation_turns,
            max_active_workstreams=args.max_active_workstreams,
            max_workstream_turns=args.max_workstream_turns,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_tokens=args.max_tokens,
        ),
        ask_operator=_deny_by_default,
    )
    wake_runtime = WakefulResidentRuntime(
        expert_loop=expert_loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(
            max_wake_cycles=args.max_wake_cycles,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_tokens=args.max_tokens,
        ),
    )
    manager = ResidentLongHorizonWorkManager(
        backend=work_backend,
        wake_runtime=wake_runtime,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=ResidentPortfolioConfig(
            max_objectives_selected=args.max_objectives_selected,
            max_active_objectives=args.max_active_objectives,
            max_wake_cycles=args.max_wake_cycles,
            max_workstream_turns=args.max_workstream_turns,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_tokens=args.max_tokens,
        ),
    )

    print("[proof] Starting resident long-horizon work management proof.")
    print(f"[proof] persona={getattr(persona, 'name', '') or 'default'}")
    print(f"[proof] executor={agent.llm_adapter_name}")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] max_objectives_selected={args.max_objectives_selected}")
    print(f"[proof] max_wake_cycles={args.max_wake_cycles}\n")

    run = await manager.run(AUTONOMY_MANDATE)
    portfolio_refs = await _list_refs(backend=work_backend, prefix="resident/portfolio")
    wake_refs = await _list_refs(backend=work_backend, prefix="resident/wakeful/cycles")
    artifact_refs = await _list_refs(
        backend=work_backend,
        prefix="resident/domain-expert/artifacts",
    )
    consolidation_refs = await _list_refs(
        backend=work_backend,
        prefix="resident/domain-expert/consolidations",
    )

    print("\n[proof] Resident portfolio proof complete.")
    print(f"[proof] portfolio_ref={run.portfolio_ref}")
    print(f"[proof] objectives={len(run.portfolio.objectives)}")
    print(f"[proof] discovered={len(run.discovered_objectives)}")
    print(f"[proof] selected={len(run.selected_objectives)}")
    print(f"[proof] advanced={len(run.advanced_objectives)}")
    print(f"[proof] portfolio_refs={len(portfolio_refs)}")
    print(f"[proof] wake_refs={len(wake_refs)}")
    print(f"[proof] artifact_refs={len(artifact_refs)}")
    print(f"[proof] consolidation_refs={len(consolidation_refs)}")
    print(f"[proof] decision={run.decision.value}")
    print(f"[proof] reason={run.decision_reason}")
    print(
        "[proof] portfolio_budget="
        f"turns:{run.budget.turns_used} "
        f"in:{run.budget.usage.input_tokens} "
        f"out:{run.budget.usage.output_tokens} "
        f"total:{run.budget.total_tokens}"
    )

    for objective in run.discovered_objectives[:8]:
        print(
            f"[candidate] {objective.id} status={objective.status} "
            f"kind={objective.kind} title={objective.title}"
        )
    for objective in run.selected_objectives:
        print(
            f"[selected] {objective.id} status={objective.status} "
            f"priority={objective.priority_score} reason={objective.priority_rationale}"
        )
    for objective in run.advanced_objectives:
        print(
            f"[advanced] {objective.id} status={objective.status} "
            f"proof={len(objective.proof_progress)} artifacts={len(objective.artifact_links)} "
            f"wake_links={len(objective.wake_links)}"
        )
    for ref in wake_refs:
        print(f"[wake] {ref}")
    for ref in consolidation_refs:
        print(f"[consolidation] {ref}")

    if not run.portfolio_ref:
        raise SystemExit("[proof] expected durable portfolio")
    if len(run.discovered_objectives) < 2:
        raise SystemExit("[proof] expected multiple inferred candidate objectives")
    if not run.selected_objectives:
        raise SystemExit("[proof] expected one selected objective")
    if run.selected_objectives[0].status != ResidentObjectiveStatus.ACTIVE.value:
        raise SystemExit("[proof] expected selected objective to be active")
    if not run.selected_objectives[0].priority_rationale:
        raise SystemExit("[proof] expected priority rationale")
    if not run.advanced_objectives:
        raise SystemExit("[proof] expected bounded objective advancement")
    advanced = run.advanced_objectives[0]
    if not advanced.proof_criteria or not advanced.budget_estimate:
        raise SystemExit("[proof] expected proof criteria and budget estimate")
    if not advanced.wake_links or not advanced.artifact_links:
        raise SystemExit("[proof] expected wake/artifact links on advanced objective")
    if not consolidation_refs:
        raise SystemExit("[proof] expected consolidation linked through backend")
    if run.decision.value != "stop" or "max turns reached" not in run.decision_reason:
        raise SystemExit("[proof] expected stop caused by configured portfolio budget")


if __name__ == "__main__":
    asyncio.run(_main())
