#!/usr/bin/env python
"""Run a real resident domain-expert loop proof from a mandate only."""

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
from ravn.domain.resident_continuation import ResidentPolicyObservation
from ravn.domain.resident_expert import ResidentDomainModel
from ravn.resident_continuation import LocalResidentMemory, MimirResidentMemory
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    MimirResidentDomainExpertMemory,
    ResidentDomainExpertConfig,
    ResidentDomainExpertLoop,
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
    parser.add_argument("--orientation-turns", type=int, default=1)
    parser.add_argument("--max-active-workstreams", type=int, default=1)
    parser.add_argument("--max-workstream-turns", type=int, default=1)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=900.0)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument(
        "--seed-memory-hygiene-case",
        action="store_true",
        help="Seed duplicate/stale prior domain memory before the real run.",
    )
    parser.add_argument(
        "--seed-operator-answer",
        default="",
        help="Persist an operator answer before the run so resume memory is consumed.",
    )
    parser.add_argument(
        "--expect-memory-hygiene",
        action="store_true",
        help="Require memory hygiene notes and operator-answer policy capture.",
    )
    return parser.parse_args()


async def _deny_by_default(question: str) -> str:
    print(f"\n[resident asks]\n{question}\n")
    print("[proof] No operator approval granted in proof mode; recording the question.")
    return "No approval granted during proof mode; ask again in an operator-supervised run."


async def _list_proof_refs(
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
        continuation_memory: Any = MimirResidentMemory(mimir)
        expert_memory: Any = MimirResidentDomainExpertMemory(mimir)
        memory_label = "mimir"
        local_memory_root = None
    else:
        root = Path.cwd() / ".ravn"
        continuation_memory = LocalResidentMemory(root)
        expert_memory = LocalResidentDomainExpertMemory(root)
        memory_label = str(root)
        local_memory_root = root

    if args.seed_memory_hygiene_case:
        prior_ref = await expert_memory.write_domain_model(
            ResidentDomainModel(
                mandate=KANUCK_VALLEY_MANDATE,
                current_understanding="Prior resident memory seeded for hygiene proof.",
                known_facts=(
                    "known printer list is stale",
                    "known printer list is stale",
                ),
                hypotheses=("stale: marketplace policy assumption needs review",),
                open_threads=("stale: old marketplace assumption",),
                learned_policy_observations=(
                    ResidentPolicyObservation(
                        subject="operator-answer:latest",
                        observation="Prefer PLA as the default material unless I say otherwise.",
                        source="operator_answer",
                    ),
                ),
            )
        )
        print(f"[proof] seeded_prior_domain_model={prior_ref}")
    if args.seed_operator_answer:
        answer_ref = await continuation_memory.write_operator_answer(args.seed_operator_answer)
        print(f"[proof] seeded_operator_answer={answer_ref}")

    loop = ResidentDomainExpertLoop(
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

    print("[proof] Starting resident domain-expert loop from mandate only.")
    print(f"[proof] persona={getattr(persona, 'name', '') or 'default'}")
    print(f"[proof] executor={agent.llm_adapter_name}")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] orientation_turns={args.orientation_turns}")
    print(f"[proof] max_active_workstreams={args.max_active_workstreams}")
    print(f"[proof] max_workstream_turns={args.max_workstream_turns}\n")

    run = await loop.run(KANUCK_VALLEY_MANDATE)
    consolidation_refs = await _list_proof_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/domain-expert/consolidations",
    )
    policy_refs = await _list_proof_refs(
        mimir=mimir,
        local_root=local_memory_root,
        prefix="resident/continuation/policy",
    )

    print("\n[proof] Resident domain-expert loop complete.")
    print(f"[proof] domain_model_ref={run.domain_model_ref}")
    print(f"[proof] workstreams={len(run.workstreams)}")
    print(f"[proof] artifacts={len(run.artifacts)}")
    print(f"[proof] execution_results={len(run.execution_results)}")
    print(f"[proof] consolidations={len(consolidation_refs)}")
    print(f"[proof] learned_policy_observations={len(run.domain_model.learned_policy_observations)}")
    print(f"[proof] memory_hygiene_notes={len(run.domain_model.memory_hygiene_notes)}")
    print(f"[proof] continuation_policy_refs={policy_refs}")
    print(f"[proof] decisions={len(run.decisions)}")
    print(
        "[proof] workstream_budget="
        f"turns:{run.budget.turns_used} "
        f"in:{run.budget.usage.input_tokens} "
        f"out:{run.budget.usage.output_tokens} "
        f"total:{run.budget.total_tokens}"
    )

    for workstream in run.workstreams:
        print(
            f"[workstream] {workstream.id} status={workstream.status} "
            f"artifact={workstream.selected_work_product} title={workstream.title}"
        )
    for artifact in run.artifacts:
        print(f"[artifact] {artifact.kind}: {artifact.path} — {artifact.title}")
    for consolidation_ref in consolidation_refs:
        print(f"[consolidation] {consolidation_ref}")
    for note in run.domain_model.memory_hygiene_notes:
        print(f"[memory-hygiene] {note}")
    for observation in run.domain_model.learned_policy_observations:
        print(
            "[policy-observation] "
            f"{observation.subject} | {observation.status} | "
            f"{observation.source} | {observation.observation}"
        )
    for decision in run.decisions:
        print(f"[decision] {decision.kind.value}: {decision.reason}")

    if not run.domain_model_ref:
        raise SystemExit("[proof] expected a persisted domain model")
    if not run.workstreams:
        raise SystemExit("[proof] expected at least one self-authored workstream")
    if not run.execution_results:
        raise SystemExit("[proof] expected at least one workstream execution result")
    if not run.artifacts:
        raise SystemExit("[proof] expected at least one durable expert artifact")
    if not consolidation_refs:
        raise SystemExit("[proof] expected at least one consolidation back into memory")
    if not run.decisions:
        raise SystemExit("[proof] expected a transparent final decision")
    if args.expect_memory_hygiene:
        if not run.domain_model.memory_hygiene_notes:
            raise SystemExit("[proof] expected memory hygiene notes")
        if not any(
            "deduplicated" in note.casefold() for note in run.domain_model.memory_hygiene_notes
        ):
            raise SystemExit("[proof] expected duplicate-memory hygiene evidence")
        if not any(
            "stale" in note.casefold() for note in run.domain_model.memory_hygiene_notes
        ):
            raise SystemExit("[proof] expected stale-memory hygiene evidence")
        if not run.domain_model.learned_policy_observations:
            raise SystemExit("[proof] expected learned policy observations")
        if not any(
            observation.source == "operator_answer"
            for observation in run.domain_model.learned_policy_observations
        ):
            raise SystemExit("[proof] expected operator answer policy observation")
        if not policy_refs:
            raise SystemExit("[proof] expected persisted continuation policy observation")


if __name__ == "__main__":
    asyncio.run(_main())
