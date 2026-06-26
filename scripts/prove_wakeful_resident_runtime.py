#!/usr/bin/env python
"""Run a real wakeful resident runtime proof from a mandate only."""

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
from ravn.adapters.resident_state.mimir import MimirResidentState
from ravn.config import ProjectConfig, Settings
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentTurnRecord
from ravn.resident_continuation import LocalResidentMemory
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    ResidentDomainExpertConfig,
    ResidentDomainExpertLoop,
)
from ravn.wakeful_resident import (
    LocalWakefulResidentMemory,
    WakefulResidentConfig,
    WakefulResidentRuntime,
)

KANUCK_VALLEY_MANDATE = (
    "Kanuck Valley Models is my small 3D printing company.\n"
    "You are its resident Ravn.\n"
    "Help it become easier to run, more creative, and more successful.\n"
    "Ask before spending money or operating physical machines."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="", help="Path to ravn config YAML")
    parser.add_argument("--persona", default="domain-drive", help="Resident persona name")
    parser.add_argument("--workspace", default="", help="Workspace root for proof artifacts")
    parser.add_argument("--max-wake-cycles", type=int, default=3)
    parser.add_argument("--orientation-turns", type=int, default=1)
    parser.add_argument("--max-active-workstreams", type=int, default=1)
    parser.add_argument("--max-workstream-turns", type=int, default=1)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=1800.0)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--sleep-when-idle", action="store_true")
    parser.add_argument(
        "--seed-pending-operator-question",
        default="",
        help="Persist a pending operator question before wakefulness starts.",
    )
    parser.add_argument(
        "--pending-operator-reason",
        default="Need operator judgment before continuing resident work.",
    )
    parser.add_argument(
        "--expect-waiting-for-operator",
        action="store_true",
        help="Require wakefulness to sleep without spending a resident turn.",
    )
    return parser.parse_args()


async def _deny_by_default(question: str) -> str:
    print(f"\n[resident asks]\n{question}\n")
    print("[proof] No operator approval granted in proof mode; recording the question.")
    return "No approval granted during proof mode; ask again in an operator-supervised run."


async def _list_refs(
    *,
    mimir: Any | None,
    local_root: Path | None,
    prefix: str,
) -> list[str]:
    if mimir is not None:
        pages = await mimir.list_pages(prefix=prefix)
        return sorted(getattr(page, "path", "") for page in pages if getattr(page, "path", ""))
    if local_root is None:
        return []
    base = local_root / prefix
    if not base.exists():
        return []
    return sorted(str(path.relative_to(local_root)) for path in base.glob("*.md"))


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
        continuation_memory: Any = MimirResidentState(mimir)
        expert_memory: Any = MimirResidentState(mimir)
        wake_memory: Any = MimirResidentState(mimir)
        memory_label = "mimir"
        local_memory_root = None
    else:
        root = Path.cwd() / ".ravn"
        continuation_memory = LocalResidentMemory(root)
        expert_memory = LocalResidentDomainExpertMemory(root)
        wake_memory = LocalWakefulResidentMemory(root)
        memory_label = str(root)
        local_memory_root = root

    if args.seed_pending_operator_question:
        pending_ref = await continuation_memory.write_operator_needed(
            question=args.seed_pending_operator_question,
            reason=args.pending_operator_reason,
            turn=ResidentTurnRecord(
                turn_index=1,
                prompt=KANUCK_VALLEY_MANDATE,
                response="",
                outcome_fields={},
                tool_names=(),
                usage=TokenUsage(input_tokens=0, output_tokens=0),
            ),
        )
        print(f"[proof] seeded_pending_operator_ref={pending_ref}")

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
    runtime = WakefulResidentRuntime(
        expert_loop=expert_loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        operator_memory=continuation_memory,
        config=WakefulResidentConfig(
            max_wake_cycles=args.max_wake_cycles,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_tokens=args.max_tokens,
            sleep_when_idle=args.sleep_when_idle,
        ),
    )

    print("[proof] Starting wakeful resident runtime from mandate only.")
    print(f"[proof] persona={getattr(persona, 'name', '') or 'default'}")
    print(f"[proof] executor={agent.llm_adapter_name}")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] max_wake_cycles={args.max_wake_cycles}")
    print(f"[proof] max_active_workstreams={args.max_active_workstreams}")
    print(f"[proof] max_workstream_turns={args.max_workstream_turns}\n")

    run = await runtime.run(KANUCK_VALLEY_MANDATE)
    wake_refs = await _list_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/wakeful/cycles",
    )
    artifact_refs = await _list_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/domain-expert/artifacts",
    )
    consolidation_refs = await _list_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/domain-expert/consolidations",
    )
    turn_refs = await _list_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/continuation/turns",
    )
    pending_refs = await _list_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/continuation/operator-needed",
    )

    print("\n[proof] Wakeful resident runtime complete.")
    print(f"[proof] cycles={len(run.cycles)}")
    print(f"[proof] wake_records={len(wake_refs)}")
    print(f"[proof] continuation_turns={len(turn_refs)}")
    print(f"[proof] pending_operator_refs={pending_refs}")
    print(f"[proof] artifacts={len(artifact_refs)}")
    print(f"[proof] consolidations={len(consolidation_refs)}")
    print(f"[proof] final_decision={run.final_decision.value}")
    print(f"[proof] final_reason={run.final_reason}")
    print(
        "[proof] wake_budget="
        f"cycles:{run.budget.turns_used} "
        f"in:{run.budget.usage.input_tokens} "
        f"out:{run.budget.usage.output_tokens} "
        f"total:{run.budget.total_tokens}"
    )

    for cycle in run.cycles:
        print(
            f"[cycle] {cycle.cycle_number} decision={cycle.decision.value} "
            f"reason={cycle.decision_reason}"
        )
        print(f"[attention] {cycle.attention_reason}")
        for item in cycle.work_created_or_advanced:
            print(f"[work] {item}")
        for ref in cycle.artifact_refs:
            print(f"[artifact-ref] {ref}")
        for item in cycle.runtime_audit:
            print(f"[runtime-audit] {item}")
    for ref in wake_refs:
        print(f"[wake-record] {ref}")
    for ref in turn_refs:
        print(f"[continuation-turn] {ref}")
    for ref in consolidation_refs:
        print(f"[consolidation] {ref}")

    if args.expect_waiting_for_operator:
        if len(run.cycles) != 1:
            raise SystemExit("[proof] expected exactly one no-burn waiting cycle")
        if run.final_decision.value != "sleep":
            raise SystemExit("[proof] expected wakefulness to sleep while waiting for operator")
        if run.final_reason != "waiting_for_operator":
            raise SystemExit("[proof] expected waiting_for_operator final reason")
        if run.budget.turns_used != 0 or run.budget.total_tokens != 0:
            raise SystemExit("[proof] expected zero resident turn/token burn while waiting")
        if turn_refs:
            raise SystemExit("[proof] expected no continuation turns while waiting for operator")
        if not pending_refs:
            raise SystemExit("[proof] expected persisted pending operator marker")
        if len(wake_refs) < 1:
            raise SystemExit("[proof] expected persisted wake record for waiting cycle")
        return

    if len(run.cycles) < args.max_wake_cycles:
        raise SystemExit("[proof] expected runtime to persist every configured wake cycle")
    if len(wake_refs) < args.max_wake_cycles:
        raise SystemExit("[proof] expected at least one wake record per cycle")
    if not artifact_refs:
        raise SystemExit("[proof] expected at least one durable expert artifact")
    if not consolidation_refs:
        raise SystemExit("[proof] expected at least one consolidation back into memory")
    if run.final_decision.value != "stop":
        raise SystemExit("[proof] expected runtime to stop on configured wake budget")
    if "max turns reached" not in run.final_reason:
        raise SystemExit("[proof] expected final stop to come from configured wake budget")


if __name__ == "__main__":
    asyncio.run(_main())
