#!/usr/bin/env python
"""Run a real resident continuation proof from a mandate only."""

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
from ravn.domain.resident_continuation import ContinuationDecisionKind
from ravn.resident_continuation import (
    LocalResidentMemory,
    MimirResidentMemory,
    ResidentBudgetLimits,
    ResidentContinuationKernel,
    ResidentRunBudget,
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
    parser.add_argument("--workspace", default="", help="Workspace root for local persistence")
    parser.add_argument("--max-turns", type=int, default=3, help="Maximum resident turns")
    parser.add_argument(
        "--max-wall-clock-seconds",
        type=float,
        default=900.0,
        help="Maximum wall-clock seconds for the proof run",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=0,
        help="Maximum token budget if usage is available",
    )
    return parser.parse_args()


async def _deny_by_default(question: str) -> str:
    print(f"\n[resident asks]\n{question}\n")
    print("[proof] No operator approval granted in proof mode; recording the question.")
    return "No approval granted during proof mode; ask again in an operator-supervised run."


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
        memory: Any = MimirResidentMemory(mimir)
        memory_label = "mimir"
    else:
        memory = LocalResidentMemory(Path.cwd() / ".ravn")
        memory_label = str(Path.cwd() / ".ravn")

    kernel = ResidentContinuationKernel(
        agent=agent,
        persona_config=persona,
        memory=memory,
        budget=ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=args.max_turns,
                max_wall_clock_seconds=args.max_wall_clock_seconds,
                max_tokens=args.max_tokens,
            )
        ),
        ask_operator=_deny_by_default,
    )

    print("[proof] Starting resident continuation from mandate only.")
    print(f"[proof] persona={getattr(persona, 'name', '') or 'default'}")
    print(f"[proof] executor={agent.llm_adapter_name}")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] max_turns={args.max_turns}\n")

    run = await kernel.run(KANUCK_VALLEY_MANDATE)

    print("\n[proof] Resident continuation complete.")
    print(f"[proof] turns={len(run.turns)}")
    print(f"[proof] decisions={len(run.decisions)}")
    print(
        "[proof] tokens="
        f"in:{run.budget.usage.input_tokens} "
        f"out:{run.budget.usage.output_tokens} "
        f"total:{run.budget.total_tokens}"
    )
    for idx, decision in enumerate(run.decisions, start=1):
        action = decision.action.action if decision.action is not None else ""
        print(f"\n[decision {idx}] {decision.kind.value}: {decision.reason}")
        if action:
            print(f"[decision {idx}] action: {action}")
        if decision.question:
            print(f"[decision {idx}] question: {decision.question}")

    if len(run.turns) < 2:
        final = run.final_decision
        reason = final.reason if final is not None else "no final decision"
        raise SystemExit(
            f"[proof] expected more than one turn; stopped after {len(run.turns)}: {reason}"
        )

    if not any(decision.kind == ContinuationDecisionKind.CONTINUE for decision in run.decisions):
        raise SystemExit("[proof] expected at least one continue decision")


if __name__ == "__main__":
    asyncio.run(_main())
