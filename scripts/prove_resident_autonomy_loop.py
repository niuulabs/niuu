#!/usr/bin/env python
"""Run a bounded real resident autonomy loop proof."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters.channels.skuld_telegram import TelegramRavnChannel
from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.events import RavnEvent
from ravn.domain.operator_contact import (
    BroadcastThenCallbackOperatorContact,
    CallbackOperatorContact,
    ChannelOperatorContact,
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
from ravn.resident_expert import LocalResidentDomainExpertMemory, MimirResidentDomainExpertMemory
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    MimirResidentWorkItemBackend,
    ResidentAutonomyLoopConfig,
    ResidentAutonomyLoopRuntime,
)
from ravn.wakeful_resident import LocalWakefulResidentMemory, MimirWakefulResidentMemory
from skuld.channels import TelegramChannel

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
        "--cycle-sleep-seconds",
        type=float,
        default=0.0,
        help="Pause between autonomy cycles so real delegated sessions can progress.",
    )
    parser.add_argument(
        "--ask-operator",
        choices=("pending", "telegram", "approve", "none"),
        default="pending",
        help=(
            "How to handle resident operator questions: emit pending help_needed, wait "
            "for a real Telegram reply, auto-answer approval for local development, or "
            "leave unwired."
        ),
    )
    parser.add_argument(
        "--telegram-credentials",
        default="/Users/jozefvaneenbergen/.niuu/credentials/user/dev-user/telegram-main.json",
    )
    parser.add_argument(
        "--telegram-topic-mode",
        choices=("shared_chat", "fixed_topic", "topic_per_session"),
        default="topic_per_session",
    )
    parser.add_argument("--telegram-message-thread-id", type=int, default=None)
    parser.add_argument(
        "--telegram-inbound-chat-id",
        action="append",
        default=[],
        help="Extra Telegram chat ID accepted for inbound replies, e.g. operator DM chat.",
    )
    parser.add_argument(
        "--telegram-allow-any-inbound-chat",
        action="store_true",
        help="Accept the first Telegram text reply from any chat while this proof is waiting.",
    )
    parser.add_argument("--reply-timeout-seconds", type=float, default=900.0)
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


async def _telegram_operator_contact(args: argparse.Namespace) -> tuple[Any, TelegramChannel]:
    creds = json.loads(Path(args.telegram_credentials).read_text(encoding="utf-8"))
    bot_token = str(creds.get("bot_token") or "").strip()
    chat_id = str(creds.get("chat_id") or "").strip()
    if not bot_token or not chat_id:
        raise SystemExit("[proof] missing Telegram bot_token/chat_id")

    reply_event = asyncio.Event()
    reply_payload: dict[str, Any] = {}
    reply_text: dict[str, str] = {"value": ""}
    accepting_replies = False
    accept_replies_after: datetime | None = None

    async def on_telegram_message(message: dict[str, Any]) -> None:
        nonlocal accepting_replies, accept_replies_after
        print(f"[proof] inbound_telegram={message}")
        if message.get("type") != "message":
            return
        if not accepting_replies:
            print("[proof] inbound_telegram_ignored=before_operator_wait")
            return
        message_date = _parse_message_date(str(message.get("date") or ""))
        if accept_replies_after is not None and message_date is not None:
            if message_date < accept_replies_after:
                print("[proof] inbound_telegram_ignored=stale_before_operator_wait")
                return
        content = str(message.get("content") or "").strip()
        if not content:
            return
        reply_text["value"] = content
        reply_payload.update(message)
        reply_event.set()

    telegram = TelegramChannel(
        bot_token=bot_token,
        chat_id=chat_id,
        notify_only=False,
        topic_mode=args.telegram_topic_mode,
        message_thread_id=args.telegram_message_thread_id,
        topic_name="Resident autonomy operator proof",
        inbound_chat_ids=args.telegram_inbound_chat_id,
        allow_any_inbound_chat=args.telegram_allow_any_inbound_chat,
        on_message=on_telegram_message,
    )
    await telegram.start()
    channel = TelegramRavnChannel(telegram)

    async def await_reply(question: str) -> str:
        nonlocal accepting_replies, accept_replies_after
        accepting_replies = True
        accept_replies_after = datetime.now(UTC)
        await telegram.send_event(
            {
                "type": "room_notification",
                "notificationType": "help_needed",
                "participant": {
                    "display_name": "Resident Ravn",
                    "persona": "resident-autonomy",
                    "participantId": "resident-autonomy-proof",
                },
                "summary": "Resident is waiting for your Telegram reply.",
                "reason": question,
                "recommendation": (
                    "Reply with approval or denial for this specific operator question. "
                    "Use words like yes/approve or no/deny so the resident can continue."
                ),
            }
        )
        print(f"[proof] telegram_last_send_wait_prompt={telegram.last_send_results}")
        print("[proof] Waiting for a real Telegram reply...")
        try:
            await asyncio.wait_for(reply_event.wait(), timeout=args.reply_timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError("timed out waiting for real Telegram reply") from exc
        print(f"[proof] reply_payload={reply_payload}")
        return reply_text["value"]

    contact = BroadcastThenCallbackOperatorContact(
        broadcast=ChannelOperatorContact(
            channel=channel,
            source="resident-autonomy-proof",
            persona="resident-proof-ravn",
            session_id="resident-autonomy-proof",
        ),
        callback=CallbackOperatorContact(
            ask_operator=await_reply,
            approval_decider=_operator_reply_grants_approval,
        ),
    )
    return contact, telegram


def _build_executor(settings: Settings) -> Any:
    cfg = settings.resident_delegation_execution
    cls = import_class(cfg.adapter)
    kwargs = resolve_secret_kwargs(dict(cfg.kwargs), dict(cfg.secret_kwargs_env))
    return cls(**kwargs)


def _parse_message_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _operator_reply_grants_approval(answer: str) -> bool | None:
    normalized = f" {answer.casefold().strip()} "
    approval_tokens = (" approve", " approved", " yes", " proceed", " allow")
    if any(token in normalized for token in approval_tokens):
        return True
    if any(token in normalized for token in (" deny", " denied", " no", " reject", " stop")):
        return False
    return None


async def _main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

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
        expert_memory: Any = MimirResidentDomainExpertMemory(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        wake_memory = LocalWakefulResidentMemory(local_root)
        expert_memory = LocalResidentDomainExpertMemory(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate)

    telegram: TelegramChannel | None = None
    ask_operator = _ask_operator(args.ask_operator)
    if args.ask_operator == "telegram":
        ask_operator, telegram = await _telegram_operator_contact(args)

    try:
        run = await ResidentAutonomyLoopRuntime(
            backend=backend,
            executor=_build_executor(settings),
            ask_operator=ask_operator,
            wake_memory=wake_memory,
            expert_memory=expert_memory,
            config=ResidentAutonomyLoopConfig(
                max_cycles=max(0, int(args.cycles)),
                max_delegations_per_cycle=max(0, int(args.delegations_per_cycle)),
                max_observations_per_cycle=max(
                    1,
                    int(settings.resident_delegation_execution.max_observations),
                ),
                sleep_between_cycles_seconds=max(0.0, float(args.cycle_sleep_seconds)),
                max_retry_follow_up_depth=max(
                    0,
                    int(settings.resident_delegation_execution.max_retry_follow_up_depth),
                ),
                approved_risk_objective_ids=tuple(
                    settings.resident_delegation_execution.approved_risk_objective_ids
                ),
                abandon_after_seconds=max(
                    0.0,
                    float(settings.resident_delegation_execution.abandon_after_seconds),
                ),
                reconcile_duplicate_delegations=bool(
                    settings.resident_delegation_execution.reconcile_duplicate_delegations
                ),
            ),
        ).run(mandate)
    finally:
        if telegram is not None:
            await telegram.close()
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
    if args.ask_operator == "telegram":
        print("[proof] telegram_notify_only=False")
        print(f"[proof] telegram_topic_mode={args.telegram_topic_mode}")
        if telegram is not None:
            print(
                "[proof] telegram_message_thread_id="
                f"{telegram.communication_route().get('thread_id')}"
            )
            print(f"[proof] telegram_last_send_results={telegram.last_send_results}")
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
    if args.ask_operator == "telegram":
        if not run.operator_contacts:
            raise SystemExit("[proof] expected answered Telegram operator contact")
        if not any(contact.status == "answered" for contact in run.operator_contacts):
            raise SystemExit("[proof] expected answered Telegram operator contact status")
        if not any(contact.approved is True for contact in run.operator_contacts):
            raise SystemExit("[proof] expected Telegram operator approval")
        if not any(ref.startswith("resident/operator-contacts/") for ref in run.persisted_refs):
            raise SystemExit("[proof] expected persisted Telegram operator contact")
    if len(run.cycles) < 2:
        raise SystemExit("[proof] expected at least two autonomy cycles")
    if not any(cycle.delegation_report.created_delegations for cycle in run.cycles):
        raise SystemExit("[proof] expected delegated execution")
    local_backends = {"local-simulated", "local-subprocess"}
    if not args.allow_local_backend and any(
        delegation.backend_name in local_backends
        for cycle in run.cycles
        for delegation in cycle.delegation_report.created_delegations
    ):
        raise SystemExit("[proof] local delegation backends do not count as real proof")
    if args.require_real_session and not any(
        delegation.backend_name not in local_backends and delegation.backend_session_id
        for cycle in run.cycles
        for delegation in cycle.delegation_report.created_delegations
    ):
        raise SystemExit("[proof] expected at least one real delegated worker session")
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
