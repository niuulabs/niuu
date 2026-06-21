#!/usr/bin/env python
"""Run a bounded resident capability discovery proof."""

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
from ravn.resident_portfolio import (
    LocalCapabilityDiscoveryBackend,
    LocalResidentWorkItemBackend,
    MimirResidentWorkItemBackend,
    ResidentCapabilityDiscoveryConfig,
    ResidentCapabilityDiscoveryRuntime,
    detect_capability_gaps,
    render_capability_discovery_result,
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


def _objective() -> ResidentObjective:
    return ResidentObjective(
        id="generic-capability-gap",
        title="Resolve generic missing capability",
        purpose="Find a safe path for a capability the resident does not yet have.",
        serves_mandate_because="The resident needs to expand capability without being told how.",
        expected_outcome="A discovery artifact and safe follow-up objectives exist.",
        proof_criteria=("Discovery artifact is persisted and linked.",),
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        required_capabilities=("generic evidence review",),
        risk_boundaries=("physical_operation",),
        source_evidence=("missing capability: generic evidence review",),
        reasoning="No safe execution path exists until the resident discovers options.",
    )


async def _seed_portfolio(backend: Any, mandate: str) -> None:
    objective = _objective()
    await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=(objective,),
            decision_history=("seeded resident capability discovery proof portfolio",),
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

    before_gaps = detect_capability_gaps(tuple(await backend.list_objectives(mandate)))
    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
        config=ResidentCapabilityDiscoveryConfig(max_options=4, max_follow_up_objectives=4),
    ).run(mandate)
    after_objectives = await backend.list_objectives(mandate)

    print("[proof] Resident capability discovery proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] gaps_before={len(before_gaps)}")
    print(
        f"[proof] selected_gap={report.selected_gap.capability if report.selected_gap else 'none'}"
    )
    print(f"[proof] persisted_refs={len(report.persisted_refs)}")
    print(f"[proof] objectives_after={len(after_objectives)}")
    print(f"[proof] operator_questions={len(report.operator_questions)}")
    print(f"[proof] final_suggested_next_action={report.final_suggested_next_action}")
    if report.discovery_result is not None:
        print(f"[proof] options={len(report.discovery_result.candidate_options)}")
        for option in report.discovery_result.candidate_options:
            print(
                "[proof] option="
                f"{option.id} approval={option.approval_required} "
                f"experiment={option.safe_next_experiment!r}"
            )
        print()
        print(render_capability_discovery_result(report.discovery_result))

    if not seeded:
        return
    if not before_gaps:
        raise SystemExit("[proof] expected seeded portfolio capability gap")
    if report.discovery_result is None:
        raise SystemExit("[proof] expected discovery result")
    if not report.discovery_result.candidate_options:
        raise SystemExit("[proof] expected candidate options")
    if not report.discovery_result.recommended_safe_next_experiment:
        raise SystemExit("[proof] expected recommended safe experiment")
    if not any(ref.startswith("resident/capability-discovery/") for ref in report.persisted_refs):
        raise SystemExit("[proof] expected persisted discovery artifact")
    if not any(
        objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
        for objective in after_objectives
    ):
        raise SystemExit("[proof] expected operator-gated follow-up objective")
    if not any(
        objective.status == ResidentObjectiveStatus.CANDIDATE.value
        and objective.kind == ResidentObjectiveKind.VERIFICATION.value
        for objective in after_objectives
    ):
        raise SystemExit("[proof] expected safe evaluation follow-up objective")
    source = next(
        (objective for objective in after_objectives if objective.id == "generic-capability-gap"),
        None,
    )
    if source is None or not source.artifact_links:
        raise SystemExit("[proof] expected source objective linked to discovery artifact")


if __name__ == "__main__":
    asyncio.run(_main())
