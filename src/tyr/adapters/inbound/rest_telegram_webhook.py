"""Telegram Bot webhook — receives updates and dispatches commands.

POST /api/v1/tyr/telegram/webhook processes incoming Telegram messages,
authenticates the sender via notification_subscriptions (chat_id → owner_id),
and executes the requested command against Tyr's domain services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response, status

from tyr.config import ReviewConfig, TelegramConfig
from tyr.domain.services.dispatch_service import DispatchItem, DispatchService
from tyr.domain.services.raid_review import (
    InvalidRaidStateError,
    RaidReviewService,
)
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.notification_subscriptions import NotificationSubscriptionRepository
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


TELEGRAM_API = "https://api.telegram.org"

HELP_TEXT = (
    "*Available commands:*\n"
    "`/status` — active sagas, pending reviews, running sessions\n"
    "`/list [saga-slug] [limit]` — list raids ready for dispatch\n"
    "`/approve <id>` — approve a raid in review\n"
    "`/reject <id> [reason]` — reject a raid\n"
    "`/retry <id>` — re-dispatch a raid\n"
    "`/pause` — pause the autonomous dispatcher\n"
    "`/resume` — resume the dispatcher\n"
    "`/dispatch <id> [model] [runtime]` — spawn a session for a ready raid\n"
    "`/sessions` — list running Volundr sessions\n"
    "`/say <session> <message>` — send a message to a session\n"
    "`/help` — show this message"
)


# ---------------------------------------------------------------------------
# Telegram reply client
# ---------------------------------------------------------------------------


class TelegramReplyClient:
    """Wraps a shared httpx.AsyncClient for sending Telegram replies."""

    def __init__(self, bot_token: str, timeout: float) -> None:
        self._bot_token = bot_token
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, chat_id: str, text: str) -> None:
        if not self._bot_token:
            logger.warning("Cannot reply — bot_token not configured")
            return
        url = f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage"
        # Telegram's legacy "Markdown" parser is permissive: unmatched marks
        # render verbatim instead of erroring, so we don't need to escape
        # dynamic content (tracker IDs, session names) defensively.
        payload: dict[str, str] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 400:
                # Markdown parse errors return 400 — fall back to plain text
                # so the user still gets the reply.
                logger.warning(
                    "Telegram rejected Markdown reply (%s); retrying as plain text",
                    resp.text[:200],
                )
                await self._client.post(
                    url,
                    json={"chat_id": chat_id, "text": text},
                )
        except Exception:
            logger.warning(
                "Failed to send Telegram reply to %s",
                _sanitize_log(chat_id),
                exc_info=True,
            )

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: list[str]
    raw_text: str


def parse_command(text: str) -> ParsedCommand | None:
    """Parse a Telegram /command message. Returns None if not a command."""
    text = text.strip()
    if not text.startswith("/"):
        return None

    # Strip bot mention suffix (e.g. /status@TyrBot)
    parts = text.split(None, 1)
    command_part = parts[0]
    # Strip bot mention suffix (e.g. /status@TyrBot) — split on @ to avoid regex DoS
    command_name = command_part.split("@")[0].lstrip("/").lower()

    rest = parts[1] if len(parts) > 1 else ""
    args = rest.split() if rest else []

    return ParsedCommand(name=command_name, args=args, raw_text=rest)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _handle_status(
    owner_id: str,
    _cmd: ParsedCommand,
    *,
    saga_repo: SagaRepository,
    volundr: VolundrPort,
    dispatcher_repo: DispatcherRepository,
) -> str:
    sagas = await saga_repo.list_sagas(owner_id=owner_id)
    active_sagas = [s for s in sagas if s.status == "ACTIVE"]

    sessions = await volundr.list_sessions()

    dispatcher = await dispatcher_repo.get_or_create(owner_id)

    lines: list[str] = []
    lines.append(f"*Dispatcher:* _{('running' if dispatcher.running else 'paused')}_")
    lines.append(f"*Active sagas:* {len(active_sagas)}")
    for saga in active_sagas[:5]:
        lines.append(f"  • *{saga.name}* (`{saga.slug}`)")
    lines.append(f"*Running sessions:* {len(sessions)}")
    for sess in sessions[:5]:
        lines.append(f"  • `{sess.id}` — *{sess.name}* _[{sess.status}]_")

    return "\n".join(lines)


async def _handle_approve(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    tracker: TrackerPort,
    review_service: RaidReviewService,
) -> str:
    if not cmd.args:
        return "*Usage:* `/approve <raid-tracker-id>`"

    tracker_id = cmd.args[0]
    raid = await _find_raid_by_tracker_id(tracker, tracker_id, owner_id)
    if raid is None:
        return f"Raid not found: `{tracker_id}`"

    try:
        result = await review_service.approve(raid.id)
    except InvalidRaidStateError as exc:
        return (
            f"Raid `{tracker_id}` is in _{exc.current}_ state — "
            "can only approve from *REVIEW*"
        )

    suffix = ""
    if result.phase_gate_unlocked:
        suffix = "\n_Phase gate unlocked_ — all raids in phase merged."
    return f"✅ Raid `{tracker_id}` approved — status → *MERGED*{suffix}"


async def _handle_reject(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    tracker: TrackerPort,
    review_service: RaidReviewService,
) -> str:
    if not cmd.args:
        return "*Usage:* `/reject <raid-tracker-id> [reason]`"

    tracker_id = cmd.args[0]
    reason = " ".join(cmd.args[1:]) if len(cmd.args) > 1 else None
    # Strip surrounding quotes from reason
    if reason:
        reason = reason.strip("\"'")

    raid = await _find_raid_by_tracker_id(tracker, tracker_id, owner_id)
    if raid is None:
        return f"Raid not found: `{tracker_id}`"

    try:
        await review_service.reject(raid.id, reason=reason)
    except InvalidRaidStateError as exc:
        return (
            f"Raid `{tracker_id}` is in _{exc.current}_ state — "
            "can only reject from *REVIEW*"
        )

    suffix = f" — reason: _{reason}_" if reason else ""
    return f"✗ Raid `{tracker_id}` rejected — status → *FAILED*{suffix}"


async def _handle_retry(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    tracker: TrackerPort,
    review_service: RaidReviewService,
) -> str:
    if not cmd.args:
        return "*Usage:* `/retry <raid-tracker-id>`"

    tracker_id = cmd.args[0]
    raid = await _find_raid_by_tracker_id(tracker, tracker_id, owner_id)
    if raid is None:
        return f"Raid not found: `{tracker_id}`"

    try:
        result = await review_service.retry(raid.id)
    except InvalidRaidStateError as exc:
        return (
            f"Raid `{tracker_id}` is in _{exc.current}_ state — "
            "can only retry from *REVIEW* or *FAILED*"
        )

    return (
        f"↻ Raid `{tracker_id}` queued for retry — "
        f"status → *{result.raid.status.value}*"
    )


async def _handle_pause(
    owner_id: str,
    _cmd: ParsedCommand,
    *,
    dispatcher_repo: DispatcherRepository,
) -> str:
    state = await dispatcher_repo.get_or_create(owner_id)
    if not state.running:
        return "Dispatcher is already _paused_."
    await dispatcher_repo.update(owner_id, running=False)
    return "⏸ Dispatcher *paused*."


async def _handle_resume(
    owner_id: str,
    _cmd: ParsedCommand,
    *,
    dispatcher_repo: DispatcherRepository,
) -> str:
    state = await dispatcher_repo.get_or_create(owner_id)
    if state.running:
        return "Dispatcher is already _running_."
    await dispatcher_repo.update(owner_id, running=True)
    return "▶ Dispatcher *resumed*."


_DEFAULT_LIST_LIMIT = 10
_MAX_LIST_LIMIT = 50


def _format_identifier(identifier: str, url: str) -> str:
    """Render a tracker identifier as a Markdown link when we have a URL.

    Telegram's legacy Markdown can't nest inline-code inside a link, so we
    pick: link if URL present (clickable wins), else backticks (monospace,
    easy to long-press-copy on mobile).
    """
    if url:
        return f"[{identifier}]({url})"
    return f"`{identifier}`"


async def _handle_dispatch(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    dispatch_service: DispatchService,
) -> str:
    """Spawn a Volundr session for a raid, optionally with model + runtime overrides.

    Usage: `/dispatch <tracker-id> [model] [runtime]`

    - `model` defaults to dispatch.default_model.
    - `runtime` is a session_definition key (e.g. skuldClaude, skuldCodex,
      skuldOpenCode); empty falls back to defaultDefinition.
    """
    if not cmd.args:
        return (
            "*Usage:* `/dispatch <tracker-id> [model] [runtime]`\n"
            "Example: `/dispatch NIU-100 gpt-5.5 skuldCodex`"
        )

    tracker_id = cmd.args[0]
    model_arg = cmd.args[1] if len(cmd.args) >= 2 else ""
    runtime_arg = cmd.args[2] if len(cmd.args) >= 3 else ""

    # Find the ready issue. We rely on find_ready_issues so the raid's phase
    # gate is honoured — dispatching out of order would skip phase locks.
    # Identifier comparison is case-insensitive so the user can copy/paste
    # without worrying about Linear's mixed-case prefixes.
    queue = await dispatch_service.find_ready_issues(owner_id)
    target = tracker_id.lower()
    item = next((q for q in queue if q.identifier.lower() == target), None)
    if item is None:
        return (
            f"Raid `{tracker_id}` is not ready for dispatch.\n"
            "Use `/list` to see what's ready, or check that prior-phase raids are merged."
        )

    # QueueItem.saga_id is already the saga's UUID-as-string — exactly what
    # DispatchItem.saga_id expects. No extra lookup needed.
    repo = item.repos[0] if item.repos else ""
    dispatch_item = DispatchItem(
        saga_id=item.saga_id,
        issue_id=item.issue_id,
        repo=repo,
        session_definition=runtime_arg or None,
    )
    results = await dispatch_service.dispatch_issues(
        owner_id,
        items=[dispatch_item],
        model=model_arg,
        session_definition=runtime_arg or None,
    )
    if not results:
        return f"Dispatch returned no result for `{tracker_id}`."
    res = results[0]
    id_label = _format_identifier(item.identifier, item.url)
    if res.status != "spawned":
        return f"✗ Dispatch failed for {id_label} — status: _{res.status}_"

    parts = [f"➜ Dispatched {id_label} — session `{res.session_id}`"]
    if model_arg:
        parts.append(f"model=`{model_arg}`")
    if runtime_arg:
        parts.append(f"runtime=`{runtime_arg}`")
    return " ".join(parts)


async def _handle_list(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    dispatch_service: DispatchService,
    saga_repo: SagaRepository,
) -> str:
    """List raids ready for dispatch, optionally scoped to a saga slug.

    Usage: `/list [saga-slug] [limit]`
    """
    saga_slug = cmd.args[0] if cmd.args else ""
    try:
        limit = (
            min(int(cmd.args[1]), _MAX_LIST_LIMIT) if len(cmd.args) >= 2 else _DEFAULT_LIST_LIMIT
        )
    except ValueError:
        return f"Invalid limit `{cmd.args[1]}` — must be an integer."

    saga_tracker_id: str | None = None
    if saga_slug:
        sagas = await saga_repo.list_sagas(owner_id=owner_id)
        match = next((s for s in sagas if s.slug == saga_slug), None)
        if match is None:
            return f"Saga not found: `{saga_slug}`"
        saga_tracker_id = match.tracker_id

    queue = await dispatch_service.find_ready_issues(
        owner_id, saga_tracker_id=saga_tracker_id
    )
    if not queue:
        scope = f" in saga `{saga_slug}`" if saga_slug else ""
        return f"_No ready raids{scope}._"

    truncated = queue[:limit]
    header_scope = f" in `{saga_slug}`" if saga_slug else ""
    lines = [f"*Ready raids{header_scope}* — showing {len(truncated)} of {len(queue)}:"]
    for q in truncated:
        title_short = q.title if len(q.title) <= 60 else q.title[:57] + "…"
        id_label = _format_identifier(q.identifier, q.url)
        lines.append(
            f"  • {id_label} — *{title_short}*\n"
            f"    saga=`{q.saga_slug}` phase=_{q.phase_name}_"
        )
    return "\n".join(lines)


async def _handle_sessions(
    _owner_id: str,
    _cmd: ParsedCommand,
    *,
    volundr: VolundrPort,
) -> str:
    sessions = await volundr.list_sessions()
    if not sessions:
        return "_No running sessions._"

    lines = [f"*Sessions* ({len(sessions)}):"]
    for sess in sessions:
        lines.append(f"  • `{sess.id}` — *{sess.name}* _[{sess.status}]_")
    return "\n".join(lines)


async def _handle_say(
    _owner_id: str,
    cmd: ParsedCommand,
    *,
    volundr: VolundrPort,
) -> str:
    if len(cmd.args) < 2:
        return "*Usage:* `/say <session-id> <message>`"

    session_id = cmd.args[0]
    message = " ".join(cmd.args[1:])

    session = await volundr.get_session(session_id)
    if session is None:
        return f"Session not found: `{session_id}`"

    await volundr.send_message(session_id, message)
    return f"✉ Message sent to session `{session_id}`."


# ---------------------------------------------------------------------------
# Raid lookup helper
# ---------------------------------------------------------------------------


async def _find_raid_by_tracker_id(
    tracker: TrackerPort,
    tracker_id: str,
    owner_id: str,
) -> Any:
    """Locate a raid by its tracker_id within the owner's sagas.

    Raids are identified by tracker_id (e.g. NIU-221) rather than internal UUID,
    so we search through the owner's sagas → phases → raids.
    Falls back to treating tracker_id as a UUID if the tracker supports get_raid_by_id.
    """
    from uuid import UUID

    # Try direct UUID lookup as a fallback
    try:
        raid_uuid = UUID(tracker_id)
        return await tracker.get_raid_by_id(raid_uuid)
    except ValueError:
        pass

    try:
        return await tracker.get_raid(tracker_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_telegram_webhook_router() -> APIRouter:
    """Create the Telegram webhook router.

    Dependencies (notification_sub_repo, tracker_factory, etc.) are resolved
    from ``request.app.state`` — wired by ``main.py`` lifespan.
    """
    router = APIRouter(
        prefix="/api/v1/tyr/telegram",
        tags=["Tyr Telegram"],
    )

    def _get_telegram_config(request: Request) -> TelegramConfig:
        settings = getattr(request.app.state, "settings", None)
        if settings is None:
            return TelegramConfig()
        return settings.telegram

    def _get_review_config(request: Request) -> ReviewConfig:
        settings = getattr(request.app.state, "settings", None)
        if settings is None:
            return ReviewConfig()
        return settings.review

    def _get_sub_repo(request: Request) -> NotificationSubscriptionRepository:
        return request.app.state.notification_sub_repo

    def _get_tracker_factory(request: Request) -> Any:
        return request.app.state.tracker_factory

    def _get_saga_repo(request: Request) -> SagaRepository:
        return request.app.state.saga_repo

    def _get_volundr(request: Request) -> VolundrPort:
        return request.app.state.volundr

    def _get_dispatcher_repo(request: Request) -> DispatcherRepository:
        return request.app.state.dispatcher_repo

    def _get_reply_client(request: Request) -> TelegramReplyClient:
        return request.app.state.telegram_reply_client

    def _get_dispatch_service(request: Request) -> DispatchService:
        return request.app.state.dispatch_service

    @router.post("/webhook", status_code=status.HTTP_200_OK)
    async def telegram_webhook(request: Request) -> Response:
        """Receive a Telegram Bot API update and process commands."""
        telegram_cfg = _get_telegram_config(request)

        # Validate webhook secret (X-Telegram-Bot-Api-Secret-Token)
        if not telegram_cfg.webhook_secret:
            logger.warning(
                "Telegram webhook secret not configured — webhook endpoint is unprotected"
            )
        elif (
            request.headers.get("x-telegram-bot-api-secret-token", "")
            != telegram_cfg.webhook_secret
        ):
            return Response(status_code=status.HTTP_403_FORBIDDEN)

        body = await request.json()
        reply_client = _get_reply_client(request)

        message = body.get("message")
        if message is None:
            return Response(status_code=status.HTTP_200_OK)

        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))

        if not chat_id or not text:
            return Response(status_code=status.HTTP_200_OK)

        # Authenticate: look up owner by chat_id
        sub_repo = _get_sub_repo(request)
        owner_id = await sub_repo.find_owner_by_telegram_chat_id(chat_id)

        if owner_id is None:
            await reply_client.send(
                chat_id,
                "This chat is not linked to a Tyr account. "
                "Use the Tyr web UI to set up Telegram notifications.",
            )
            return Response(status_code=status.HTTP_200_OK)

        cmd = parse_command(text)
        if cmd is None:
            return Response(status_code=status.HTTP_200_OK)

        # Resolve tracker for this owner
        tracker_factory = _get_tracker_factory(request)
        trackers = await tracker_factory.for_owner(owner_id)
        tracker = trackers[0] if trackers else None

        if tracker is None:
            await reply_client.send(chat_id, "No tracker configured.")
            return Response(status_code=status.HTTP_200_OK)

        # Dispatch command
        saga_repo = _get_saga_repo(request)
        volundr = _get_volundr(request)
        dispatcher_repo = _get_dispatcher_repo(request)
        dispatch_service = _get_dispatch_service(request)
        review_config = _get_review_config(request)
        event_bus = getattr(request.app.state, "event_bus", None)
        review_service = RaidReviewService(tracker, owner_id, review_config, event_bus=event_bus)

        try:
            reply = await _dispatch_command(
                owner_id,
                cmd,
                tracker=tracker,
                saga_repo=saga_repo,
                volundr=volundr,
                dispatcher_repo=dispatcher_repo,
                dispatch_service=dispatch_service,
                review_service=review_service,
            )
        except Exception:
            logger.exception("Error handling Telegram command: /%s", cmd.name)
            reply = f"Error executing /{cmd.name}. Please try again."

        logger.info(
            "Telegram command: /%s args=%s owner=%s reply=%r",
            cmd.name,
            cmd.args,
            owner_id[:8],
            reply[:200],
        )
        await reply_client.send(chat_id, reply)
        return Response(status_code=status.HTTP_200_OK)

    return router


async def _dispatch_command(
    owner_id: str,
    cmd: ParsedCommand,
    *,
    tracker: TrackerPort,
    saga_repo: SagaRepository,
    volundr: VolundrPort,
    dispatcher_repo: DispatcherRepository,
    dispatch_service: DispatchService,
    review_service: RaidReviewService,
) -> str:
    """Route a parsed command to the appropriate handler.

    To add a new command (e.g. /review, /confidence, /saga): add a case
    branch below, implement _handle_<name>(), and update HELP_TEXT.
    """
    match cmd.name:
        case "status":
            return await _handle_status(
                owner_id,
                cmd,
                saga_repo=saga_repo,
                volundr=volundr,
                dispatcher_repo=dispatcher_repo,
            )
        case "approve":
            return await _handle_approve(
                owner_id, cmd, tracker=tracker, review_service=review_service
            )
        case "reject":
            return await _handle_reject(
                owner_id, cmd, tracker=tracker, review_service=review_service
            )
        case "retry":
            return await _handle_retry(
                owner_id, cmd, tracker=tracker, review_service=review_service
            )
        case "pause":
            return await _handle_pause(owner_id, cmd, dispatcher_repo=dispatcher_repo)
        case "resume":
            return await _handle_resume(owner_id, cmd, dispatcher_repo=dispatcher_repo)
        case "dispatch":
            return await _handle_dispatch(
                owner_id,
                cmd,
                dispatch_service=dispatch_service,
            )
        case "list":
            return await _handle_list(
                owner_id,
                cmd,
                dispatch_service=dispatch_service,
                saga_repo=saga_repo,
            )
        case "sessions":
            return await _handle_sessions(owner_id, cmd, volundr=volundr)
        case "say":
            return await _handle_say(owner_id, cmd, volundr=volundr)
        case "help" | "start":
            return HELP_TEXT
        case _:
            return f"Unknown command: /{cmd.name}\n\n{HELP_TEXT}"
