"""Attention notifier — fans a 'session needs you' push out to the owner.

Implements the :class:`~volundr.domain.ports.AttentionNotifier` port the
session service calls when a session enters ``awaiting_input``. It resolves the
owner's registered devices and dispatches through the configured
:class:`~volundr.domain.ports.NotificationChannel`.
"""

from __future__ import annotations

import logging

from volundr.domain.models import PushMessage, Session
from volundr.domain.ports import (
    AttentionNotifier,
    DeviceTokenRepository,
    NotificationChannel,
)

logger = logging.getLogger(__name__)

# Sessions blocked on a human are always high urgency — this is the whole point
# of the signal. Kept as a named constant rather than a literal so the urgency
# gate below reads clearly.
_NEEDS_INPUT_URGENCY = 0.9

_DEFAULT_PROMPTS = {
    "question": "The agent is asking you a question",
    "confirmation": "The agent needs your confirmation",
    "permission": "The agent needs permission to continue",
}


class PushAttentionNotifier(AttentionNotifier):
    """Dispatch a push when a session needs the user.

    Args:
        device_repository: Source of the owner's registered devices.
        channel: Delivery channel (APNs / webhook / logging).
        min_urgency: Drop pushes below this urgency (lets an operator dial the
            channel down without code changes).
    """

    def __init__(
        self,
        device_repository: DeviceTokenRepository,
        channel: NotificationChannel,
        min_urgency: float = 0.8,
    ):
        self._device_repository = device_repository
        self._channel = channel
        self._min_urgency = min_urgency

    async def notify_needs_input(
        self,
        session: Session,
        *,
        kind: str,
        prompt: str,
        request_id: str,
    ) -> None:
        if _NEEDS_INPUT_URGENCY < self._min_urgency:
            return
        owner_id = session.owner_id
        if not owner_id:
            logger.debug("Skipping needs-input push for ownerless session %s", session.id)
            return

        body = prompt or _DEFAULT_PROMPTS.get(kind, "The agent needs your attention")
        message = PushMessage(
            owner_id=owner_id,
            title=f"{session.name} needs you",
            body=body,
            session_id=str(session.id),
            kind=kind,
            urgency=_NEEDS_INPUT_URGENCY,
            request_id=request_id,
        )
        try:
            devices = await self._device_repository.list_for_owner(owner_id)
            await self._channel.send(message, devices)
        except Exception:
            # A push failure must never break the session activity path.
            logger.warning(
                "Failed to dispatch needs-input push for session %s", session.id, exc_info=True
            )
