#!/usr/bin/env python
"""Prove resident operator reach-out/resume through real Skuld Telegram."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.utils import resolve_secret_kwargs
from ravn.adapters.channels.skuld_telegram import TelegramRavnChannel
from ravn.cli.commands import (
    _build_agent,
    _build_mimir,
    _configure_logging,
    _resolve_persona,
)
from ravn.adapters.resident_state.mimir import MimirResidentState
from ravn.config import ProjectConfig, Settings
from ravn.resident_continuation import LocalResidentMemory
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    ResidentDomainExpertConfig,
    ResidentDomainExpertLoop,
)
from skuld.channels import TelegramChannel

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
    parser.add_argument(
        "--force-local-memory",
        action="store_true",
        help="Use local .ravn proof memory even when configured Mimir adapters exist.",
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
    parser.add_argument("--orientation-turns", type=int, default=30)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=1800.0)
    parser.add_argument("--reply-timeout-seconds", type=float, default=900.0)
    return parser.parse_args()


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
    return sorted(str(path.relative_to(local_root)) for path in base.rglob("*.md"))


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

    settings = Settings()
    _configure_logging(settings)
    _preflight_llm_credentials(settings)
    project_config = ProjectConfig.discover()
    persona = _resolve_persona(args.persona, project_config, settings=settings, cwd=Path.cwd())
    agent, _unused_channel = _build_agent(settings, persona_config=persona)

    mimir = None if args.force_local_memory else _build_mimir(settings)
    if mimir is not None:
        continuation_memory: Any = MimirResidentState(mimir)
        expert_memory: Any = MimirResidentState(mimir)
        memory_label = "mimir"
        local_memory_root = None
    else:
        root = Path.cwd() / ".ravn"
        continuation_memory = LocalResidentMemory(root)
        expert_memory = LocalResidentDomainExpertMemory(root)
        memory_label = str(root)
        local_memory_root = root

    creds = json.loads(Path(args.telegram_credentials).read_text(encoding="utf-8"))
    bot_token = str(creds.get("bot_token") or "").strip()
    chat_id = str(creds.get("chat_id") or "").strip()
    if not bot_token or not chat_id:
        raise SystemExit("[proof] missing Telegram bot_token/chat_id")

    reply_event = asyncio.Event()
    reply_payload: dict[str, Any] = {}
    accepting_replies = False
    accept_replies_after: datetime | None = None

    async def on_telegram_message(message: dict[str, Any]) -> None:
        print(f"[proof] inbound_telegram={message}")
        if message.get("type") != "message":
            return
        if not accepting_replies:
            print("[proof] inbound_telegram_ignored=before_pending_question")
            return
        message_date = _parse_message_date(str(message.get("date") or ""))
        if accept_replies_after is not None and message_date is not None:
            if message_date < accept_replies_after:
                print("[proof] inbound_telegram_ignored=stale_before_pending_question")
                return
        content = str(message.get("content") or "").strip()
        if not content:
            return
        await continuation_memory.write_operator_answer(content)
        reply_payload.update(message)
        reply_event.set()

    telegram = TelegramChannel(
        bot_token=bot_token,
        chat_id=chat_id,
        notify_only=False,
        topic_mode=args.telegram_topic_mode,
        message_thread_id=args.telegram_message_thread_id,
        topic_name="Resident Ravn operator proof",
        inbound_chat_ids=args.telegram_inbound_chat_id,
        allow_any_inbound_chat=args.telegram_allow_any_inbound_chat,
        on_message=on_telegram_message,
    )
    await telegram.start()
    proof_channel = TelegramRavnChannel(telegram)
    try:
        config = ResidentDomainExpertConfig(
            orientation_turns=args.orientation_turns,
            max_active_workstreams=0,
            max_workstream_turns=0,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
        )

        loop = ResidentDomainExpertLoop(
            agent=agent,
            persona_config=persona,
            continuation_memory=continuation_memory,
            expert_memory=expert_memory,
            config=config,
            channel=proof_channel,
        )

        print("[proof] Starting resident Telegram operator round trip.")
        print(f"[proof] persona={getattr(persona, 'name', '') or 'default'}")
        print(f"[proof] executor={agent.llm_adapter_name}")
        print(f"[proof] memory={memory_label}")
        print("[proof] telegram_notify_only=False")
        print(f"[proof] telegram_topic_mode={args.telegram_topic_mode}")
        print(
            "[proof] telegram_message_thread_id="
            f"{telegram.communication_route().get('thread_id')}"
        )
        print(
            "[proof] telegram_allow_any_inbound_chat="
            f"{args.telegram_allow_any_inbound_chat}"
        )
        print(f"[proof] telegram_inbound_chat_ids={args.telegram_inbound_chat_id}")
        print(f"[proof] telegram_chat_id_suffix={chat_id[-4:]}")

        first = await loop.run(KANUCK_VALLEY_MANDATE)
        print(
            f"[proof] first_final="
            f"{first.final_decision.kind.value if first.final_decision else ''}"
        )
        print(f"[proof] first_reason={first.final_decision.reason if first.final_decision else ''}")
        print(f"[proof] telegram_help_events={len(proof_channel.sent_events)}")
        print(f"[proof] telegram_last_send_after_help={telegram.last_send_results}")

        pending_refs = await _list_refs(
            mimir=mimir,
            local_root=local_memory_root,
            prefix="resident/continuation/operator-needed",
        )
        print(f"[proof] pending_refs={pending_refs}")
        pending_question = await continuation_memory.read_operator_needed()
        pending_question_text = ""
        if pending_question is not None:
            print(f"[proof] pending_question={pending_question.summary}")
            pending_question_text = _operator_question_from_marker(pending_question.content)
            if pending_question_text:
                print(f"[proof] pending_question_text={pending_question_text}")

        first_kind = first.final_decision.kind.value if first.final_decision else ""
        first_reason = first.final_decision.reason if first.final_decision else ""
        waiting_on_existing_pending = (
            first_kind == "sleep" and first_reason == "waiting_for_operator"
        )
        if not proof_channel.sent_events and not waiting_on_existing_pending:
            raise SystemExit("[proof] expected a Telegram help_needed event")
        if first_kind not in {"ask_operator", "sleep"}:
            raise SystemExit("[proof] expected first run to ask operator")
        if first_kind == "sleep" and not waiting_on_existing_pending:
            raise SystemExit("[proof] unexpected resident sleep reason")
        if not pending_refs:
            raise SystemExit("[proof] expected a persisted pending operator marker")

        accepting_replies = True
        accept_replies_after = datetime.now(UTC)
        await telegram.send_event(
            {
                "type": "room_notification",
                "notificationType": "help_needed",
                "participant": {
                    "display_name": "Resident Ravn",
                    "persona": "domain-drive",
                    "participantId": "resident-ravn-proof",
                },
                "summary": "Resident is now waiting for your Telegram reply.",
                "reason": "The pending operator question has been persisted.",
                "recommendation": (
                    "Reply to this Telegram message, or DM @NiuuRavnBot while this proof is "
                    "waiting, with: "
                    + (
                        pending_question_text
                        or "the values or any short test answer for the proof."
                    )
                ),
            }
        )
        print(f"[proof] telegram_last_send_wait_prompt={telegram.last_send_results}")
        print("[proof] Waiting for a real Telegram reply...")
        try:
            await asyncio.wait_for(reply_event.wait(), timeout=args.reply_timeout_seconds)
        except TimeoutError as exc:
            raise SystemExit("[proof] timed out waiting for real Telegram reply") from exc

        answer_refs = await _list_refs(
            mimir=mimir,
            local_root=local_memory_root,
            prefix="resident/continuation/operator-answers",
        )
        print(f"[proof] answer_refs={answer_refs}")
        print(f"[proof] reply_payload={reply_payload}")

        resumed = await loop.run(KANUCK_VALLEY_MANDATE)
        print(
            f"[proof] resumed_final="
            f"{resumed.final_decision.kind.value if resumed.final_decision else ''}"
        )
        print(f"[proof] resumed_turns={len(resumed.domain_model.known_facts)} known_fact_entries")
        for turn_ref in await _list_refs(
            mimir=mimir,
            local_root=local_memory_root,
            prefix="resident/continuation/turns",
        ):
            print(f"[turn] {turn_ref}")
    finally:
        await telegram.close()


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


def _operator_question_from_marker(content: str) -> str:
    match = re.search(r"^- question:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _preflight_llm_credentials(settings: Settings) -> None:
    provider = settings.llm.provider
    adapter = str(provider.adapter or "")
    kwargs = resolve_secret_kwargs(
        dict(provider.kwargs or {}),
        dict(provider.secret_kwargs_env or {}),
    )
    adapter_lower = adapter.casefold()
    if adapter.endswith("AnthropicAdapter") and "bifrost" not in adapter_lower:
        if not str(kwargs.get("api_key") or "").strip():
            raise SystemExit(
                "[proof] configured Ravn LLM is AnthropicAdapter but no api_key is "
                "available. Provide a real key in the proof YAML or configure a real "
                "non-Anthropic provider before running the Telegram roundtrip."
            )
    if adapter.endswith("OpenAICompatibleAdapter"):
        base_url = str(kwargs.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        if base_url == "https://api.openai.com/v1" and not str(
            kwargs.get("api_key") or ""
        ).strip():
            raise SystemExit(
                "[proof] configured Ravn LLM is OpenAICompatibleAdapter against "
                "api.openai.com but no api_key is available. Provide a real key in "
                "the proof YAML or point base_url at an authenticated/local compatible "
                "server."
            )


if __name__ == "__main__":
    asyncio.run(_main())
