#!/usr/bin/env python
"""Run a bounded real resident autonomy loop proof."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.events import RavnEvent
from ravn.domain.operator_contact import (
    OperatorContactResult,
    answer_operator_contact,
    emit_help_needed_operator_contact,
)
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    LocalSubprocessResidentExecutor,
    MimirResidentWorkItemBackend,
    ResidentAutonomyLoopConfig,
    ResidentAutonomyLoopRuntime,
)
from ravn.wakeful_resident import LocalWakefulResidentMemory, MimirWakefulResidentMemory

AUTONOMY_MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts.\n"
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class ProofChannel:
    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.events.append(event)


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
        "--cycles",
        type=int,
        default=2,
        help="Maximum autonomy cycles to run.",
    )
    parser.add_argument(
        "--delegations-per-cycle",
        type=int,
        default=1,
        help="Maximum delegated worker launches per autonomy cycle.",
    )
    parser.add_argument(
        "--ask-operator",
        choices=("pending", "approve", "none"),
        default="pending",
        help=(
            "How to handle resident operator questions: emit pending help_needed, "
            "auto-answer approval for proof, or leave unwired."
        ),
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
        purpose=f"Advance bounded resident work around {title}.",
        serves_mandate_because="It proves the resident can advance work without babysitting.",
        expected_outcome="A real local worker result is reviewed and persisted.",
        proof_criteria=("A reviewed worker result artifact is persisted.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        risk_boundaries=risk_boundaries,
        source_evidence=(f"ready autonomy objective: {title}",),
        reasoning="This objective is ready for a bounded autonomy cycle.",
    )


async def _seed_portfolio(backend: Any, mandate: str) -> None:
    objectives = (
        _objective("autonomy-real-one", "Review generic resident evidence"),
        _objective("autonomy-real-two", "Summarize generic resident options"),
        _objective(
            "autonomy-risky",
            "Touch bounded external effect",
            risk_boundaries=("external_side_effect",),
        ),
    )
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=objectives,
            decision_history=("seeded real resident autonomy proof portfolio",),
        )
    )


def _ask_operator(mode: str) -> Any:
    if mode == "none":
        return None
    if mode == "approve":
        class ApprovingProofContact:
            async def ask(self, request: Any) -> OperatorContactResult:
                return await answer_operator_contact(
                    request,
                    lambda _question: "Approved for this bounded proof run.",
                    approval_decider=lambda answer: bool(answer.strip()),
                )

        return ApprovingProofContact()

    class PendingProofContact:
        def __init__(self) -> None:
            self._channel = ProofChannel()

        async def ask(self, request: Any) -> OperatorContactResult:
            return await emit_help_needed_operator_contact(
                self._channel,
                request,
                source="resident-autonomy-proof",
                persona="resident-proof-ravn",
                session_id="resident-autonomy-proof",
            )

    return PendingProofContact()


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
        wake_memory: Any = MimirWakefulResidentMemory(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        wake_memory = LocalWakefulResidentMemory(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate)

    run = await ResidentAutonomyLoopRuntime(
        backend=backend,
        executor=LocalSubprocessResidentExecutor(),
        ask_operator=_ask_operator(args.ask_operator),
        wake_memory=wake_memory,
        config=ResidentAutonomyLoopConfig(
            max_cycles=max(0, int(args.cycles)),
            max_delegations_per_cycle=max(0, int(args.delegations_per_cycle)),
        ),
    ).run(mandate)
    objectives = await backend.list_objectives(mandate)
    delegations = await backend.list_delegations(mandate)
    wake_records = await wake_memory.list_wake_records(mandate, limit=10)

    print("[proof] Real resident autonomy loop proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] cycles={len(run.cycles)}")
    print(f"[proof] persisted_refs={len(run.persisted_refs)}")
    print(f"[proof] operator_questions={len(run.operator_questions)}")
    print(f"[proof] operator_contacts={len(run.operator_contacts)}")
    print(f"[proof] objectives_after={len(objectives)}")
    print(f"[proof] delegations_after={len(delegations)}")
    print(f"[proof] wake_records={len(wake_records)}")
    print(f"[proof] final_suggested_next_action={run.final_suggested_next_action}")
    for cycle in run.cycles:
        report = cycle.delegation_report
        print(
            "[proof] cycle="
            f"{cycle.cycle_number} selected={len(cycle.selected_objectives)} "
            f"launched={len(report.created_delegations)} "
            f"observed={len(report.observed_results)} reviews={len(cycle.review_decisions)} "
            f"questions={len(cycle.operator_questions)}"
        )
        for delegation in report.created_delegations:
            print(
                "[proof] real_session="
                f"{delegation.backend_name}:{delegation.backend_session_id} "
                f"objective={delegation.source_objective_id}"
            )
        for review in cycle.review_decisions:
            print(f"[proof] review={review.id} decision={review.decision} reason={review.reason}")
        for result in report.observed_results:
            print(f"[proof] result={result.session_id}: {result.summary}")
        for question in cycle.operator_questions:
            print(f"[proof] operator_question={question}")
        for contact in cycle.operator_contacts:
            print(
                "[proof] operator_wait="
                f"{contact.request.id} status={contact.status} approved={contact.approved} "
                f"emitted_ref={contact.emitted_ref}"
            )

    if not seeded:
        return
    if args.ask_operator == "pending":
        if not run.operator_contacts:
            raise SystemExit("[proof] expected pending operator contact")
        if not any(contact.status == "pending" for contact in run.operator_contacts):
            raise SystemExit("[proof] expected pending operator contact status")
        if len(run.cycles) != 1:
            raise SystemExit("[proof] expected loop to wait after pending operator contact")
        if not any(ref.startswith("resident/operator-contacts/") for ref in run.persisted_refs):
            raise SystemExit("[proof] expected persisted operator contact")
        return
    if len(run.cycles) < 2:
        raise SystemExit("[proof] expected at least two autonomy cycles")
    if not any(cycle.delegation_report.created_delegations for cycle in run.cycles):
        raise SystemExit("[proof] expected delegated execution")
    if not any(
        delegation.backend_name == "local-subprocess"
        for cycle in run.cycles
        for delegation in cycle.delegation_report.created_delegations
    ):
        raise SystemExit("[proof] expected non-simulated local subprocess backend")
    if not any(cycle.delegation_report.observed_results for cycle in run.cycles):
        raise SystemExit("[proof] expected observed delegated result")
    if not any(cycle.review_decisions for cycle in run.cycles):
        raise SystemExit("[proof] expected delegated result review")
    if not any(ref.startswith("resident/delegations/") for ref in run.persisted_refs):
        raise SystemExit("[proof] expected persisted delegation record")
    if not any(ref.startswith("resident/delegation-results/") for ref in run.persisted_refs):
        raise SystemExit("[proof] expected persisted delegation result")
    if not any(ref.startswith("resident/delegation-reviews/") for ref in run.persisted_refs):
        raise SystemExit("[proof] expected persisted delegation review")
    if not any(ref.startswith("resident/wakeful/cycles/") for ref in run.persisted_refs):
        raise SystemExit("[proof] expected persisted wake cycle")
    if not any(
        objective.title.startswith("Follow up delegated result") for objective in objectives
    ):
        raise SystemExit("[proof] expected follow-up objective")
    if not any(
        objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value for objective in objectives
    ):
        raise SystemExit("[proof] expected risky objective routed to operator")


if __name__ == "__main__":
    asyncio.run(_main())
