"""Runtime notification channel contributor for active sessions.

Reuses existing user-configured integrations as the control-plane source of
truth, then projects the resolved runtime channel configuration into the Skuld
session spec.
"""

from __future__ import annotations

import logging
from typing import Any

from niuu.domain.models import IntegrationConnection, IntegrationType
from volundr.domain.models import Session
from volundr.domain.ports import (
    CredentialStorePort,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

logger = logging.getLogger(__name__)

_TELEGRAM_TOPIC_MODES = {"shared_chat", "fixed_topic", "topic_per_session"}


class NotificationChannelContributor(SessionContributor):
    """Inject runtime notification channel config for flock sessions.

    Phase 1 keeps the runtime channel resolution intentionally narrow:
    - reuse the existing user messaging integration connections
    - resolve Telegram credentials from the existing credential store
    - inject Skuld broker config so the active room can notify externally

    The contributor defaults Telegram to notify-only mode so multiple active
    sessions do not contend for the same bot's polling stream.
    """

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort | None = None,
        **_extra: object,
    ) -> None:
        self._credential_store = credential_store

    @property
    def name(self) -> str:
        return "notification_channels"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if context.workload_type != "ravn_flock":
            return SessionContribution()

        if (
            not session.owner_id
            or not context.integration_connections
            or not self._credential_store
        ):
            return SessionContribution()

        telegram_cfg = await self._resolve_telegram_config(
            session.owner_id,
            context.integration_connections,
        )
        if telegram_cfg is None:
            return SessionContribution()

        return SessionContribution(values={"broker": {"telegram": telegram_cfg}})

    async def _resolve_telegram_config(
        self,
        owner_id: str,
        connections: tuple[IntegrationConnection, ...],
    ) -> dict[str, Any] | None:
        for conn in connections:
            if conn.integration_type != IntegrationType.MESSAGING:
                continue
            if conn.slug != "telegram":
                continue

            secret = await self._credential_store.get_value("user", owner_id, conn.credential_name)
            if not secret:
                logger.debug(
                    "Skipping Telegram runtime channel %s — credential %s not found",
                    conn.id,
                    conn.credential_name,
                )
                continue

            bot_token = str(secret.get("bot_token", "")).strip()
            chat_id = str(secret.get("chat_id", "")).strip()
            if not bot_token or not chat_id:
                logger.debug(
                    "Skipping Telegram runtime channel %s — bot_token/chat_id missing",
                    conn.id,
                )
                continue

            notify_only = bool(conn.config.get("notify_only", True))
            message_thread_id = conn.config.get("message_thread_id")
            resolved_thread_id: int | None = None
            if message_thread_id not in (None, ""):
                try:
                    resolved_thread_id = int(message_thread_id)
                except (TypeError, ValueError):
                    logger.debug(
                        "Skipping invalid Telegram message_thread_id=%r on %s",
                        message_thread_id,
                        conn.id,
                    )

            topic_mode_raw = conn.config.get("topic_mode")
            if topic_mode_raw is not None:
                topic_mode = str(topic_mode_raw).strip().lower()
                if topic_mode not in _TELEGRAM_TOPIC_MODES:
                    logger.debug(
                        "Unknown Telegram topic_mode %r on %s, defaulting to topic_per_session",
                        topic_mode,
                        conn.id,
                    )
                    topic_mode = "topic_per_session"
            elif resolved_thread_id is not None:
                topic_mode = "fixed_topic"
            elif bool(conn.config.get("topic_per_session", True)):
                topic_mode = "topic_per_session"
            else:
                topic_mode = "shared_chat"

            telegram_cfg: dict[str, Any] = {
                "enabled": True,
                "botToken": bot_token,
                "chatId": chat_id,
                "notifyOnly": notify_only,
                "topicMode": topic_mode,
            }
            if resolved_thread_id is not None:
                telegram_cfg["messageThreadId"] = resolved_thread_id
            return telegram_cfg

        return None
