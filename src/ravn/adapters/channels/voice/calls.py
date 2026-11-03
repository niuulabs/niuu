"""Calls the resident places itself: ``notify`` with ``speak`` rings the owner's phone.

The agent asks to be heard with the ``notify`` tool's ``speak`` intent. The tool
queues the notification as always (the alert and the feed record stay the fallback)
and queues a call request here, in the voice store (:class:`ResidentCallRequests`).
It writes the request itself rather than leaving it for a watcher to find in the
notification outbox: Forge drains and deletes outbox entries as soon as it reads them.

:class:`CallRequester` is the single decider. It runs with the voice gateway, looks
at queued requests about once a second (a request expires after
``expires_after_seconds``; there is no rate cap or gap between calls) and delivers
each request once as a ``call.request`` frame:

* over the phone's socket, when it holds a fresh standby (or in-call) presence;
* otherwise, or when the phone sends no ``call.result`` within
  ``result_timeout_seconds``, by a VoIP push through :class:`CallPushPort`, once,
  with the same request id (the phone handles an id once).

The phone answers ``call.result`` on whichever socket it has then; the gateway
records it by request id (:meth:`VoiceCallStore.record_call_result`). The phone still
applies its own allow-list, Focus and quiet hours. Logs carry request ids,
deliveries and outcomes only, never the text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from niuu.domain.notifications import NotificationSpeak, notification_id
from ravn.adapters.channels.voice.store import CallRequest, VoiceCallStore, now_ms
from ravn.config import VoiceCallsConfig
from ravn.ports.call_push import CallPushPort

logger = logging.getLogger(__name__)

CALL_REQUEST = "call.request"
#: ``source`` of a resident's request: the phone binds it to the resident it selected.
SOURCE = "resident"
#: The phone's device kind that takes calls (the Mac sends no presence).
CALL_DEVICE = "iphone"
#: Seconds between the phone's frames when its presence names none: it pings every 30 s.
DEFAULT_HEARTBEAT_SECONDS = 60.0

#: Forge's session-key grammar for a resident session (``volundr`` ``openclaw_gateway``):
#: any other channel, or none, is the resident's main room.
_SESSION_KEY_PREFIX = "agent:main:niuu-"

SendFrame = Callable[[dict[str, Any]], Awaitable[bool]]


# -- the notification a request belongs to --------------------------------


def resident_session_id(channel: str | None) -> uuid.UUID | None:
    """The Forge resident session a notify channel names; ``None`` for the main room."""
    if not channel or not channel.startswith(_SESSION_KEY_PREFIX):
        return None
    try:
        return uuid.UUID(channel.removeprefix(_SESSION_KEY_PREFIX))
    except ValueError:
        return None


def resident_notification_id(resident_id: str, channel: str | None, idempotency_key: str) -> str:
    """The id Forge gives the notification a resident's ``notify`` queued.

    The same derivation as Forge's (``resident_notify_dedupe_key`` and
    ``notification_id``), so the phone's call and the feed's row name one notification
    without asking Forge.
    """
    session = resident_session_id(channel)
    anchor = f"{uuid.UUID(resident_id)}:{session or 'room'}"
    return str(notification_id(f"resident-notify:{anchor}:{idempotency_key}"))


class ResidentCallRequests:
    """The ``notify`` tool's side: queue a call request for a notification with ``speak``."""

    def __init__(
        self,
        store: VoiceCallStore,
        *,
        resident_id: str,
        expires_after_seconds: int,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        uuid.UUID(resident_id)  # a bad id fails at start, not on the first call
        self._store = store
        self._resident_id = resident_id
        self._expires_after_ms = int(expires_after_seconds) * 1000
        self._clock = clock

    def close(self) -> None:
        self._store.close()

    def request(
        self, *, speak: NotificationSpeak, idempotency_key: str, channel: str | None
    ) -> CallRequest | None:
        """Queue it; ``None`` when this notification already asked (a retried notify)."""
        created = self._clock()
        request = self._store.queue_call_request(
            request_id=str(uuid.uuid4()),
            notification_id=resident_notification_id(self._resident_id, channel, idempotency_key),
            idempotency_key=idempotency_key,
            channel=channel,
            text=speak.text,
            priority=speak.priority.value,
            created_at_ms=created,
            expires_at_ms=created + self._expires_after_ms,
        )
        if request is not None:
            logger.info(
                "Call request %s queued (priority=%s)", request.request_id, request.priority
            )
        return request


# -- the phone's presence ----------------------------------------------------


@dataclass
class CallPresence:
    """The latest ``call.presence`` of one install, and the socket it came on."""

    device_id: str
    standby: bool
    active_call: bool
    heartbeat_seconds: float
    #: Monotonic time of the last frame on its socket (any frame: pings count).
    seen_at: float
    connection: object
    send: SendFrame

    @property
    def can_take_a_call(self) -> bool:
        return self.standby or self.active_call


class CallPresenceRegistry:
    """The phones that can take a call, by install id; a newer socket replaces an older one."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_device: dict[str, CallPresence] = {}

    def __len__(self) -> int:
        return len(self._by_device)

    def update(self, payload: dict[str, Any], *, connection: object, send: SendFrame) -> bool:
        """Record a ``call.presence``; ``False`` for a device that takes no calls.

        Raises ``ValueError`` for a presence without a ``deviceId``.
        """
        device_id = payload.get("deviceId")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("deviceId is required")
        if str(payload.get("device") or CALL_DEVICE) != CALL_DEVICE:
            return False
        liveness = payload.get("liveness") if isinstance(payload.get("liveness"), dict) else {}
        heartbeat = liveness.get("heartbeatSeconds")
        self._by_device[device_id] = CallPresence(
            device_id=device_id,
            standby=payload.get("standby") is True,
            active_call=payload.get("activeCall") is True,
            heartbeat_seconds=(
                float(heartbeat)
                if isinstance(heartbeat, int | float) and heartbeat > 0
                else DEFAULT_HEARTBEAT_SECONDS
            ),
            seen_at=self._clock(),
            connection=connection,
            send=send,
        )
        return True

    def touch(self, connection: object) -> None:
        """A frame arrived on *connection*: its presence is still alive."""
        now = self._clock()
        for presence in self._by_device.values():
            if presence.connection is connection:
                presence.seen_at = now

    def drop(self, connection: object) -> None:
        """*connection* closed: the presences it carried are gone."""
        for device_id in [
            device_id
            for device_id, presence in self._by_device.items()
            if presence.connection is connection
        ]:
            del self._by_device[device_id]

    def fresh(self) -> CallPresence | None:
        """The most recently seen phone that can take a call now, if its socket is alive.

        Alive means a frame within two heartbeats.
        """
        now = self._clock()
        candidates = [
            presence
            for presence in self._by_device.values()
            if presence.can_take_a_call and now - presence.seen_at <= 2 * presence.heartbeat_seconds
        ]
        return max(candidates, key=lambda presence: presence.seen_at, default=None)


# -- the frame ---------------------------------------------------------------


def call_request_frame(
    request: CallRequest, *, host: str, resident_id: str, name: str
) -> dict[str, Any]:
    """The ``call.request`` frame: the socket frame and the VoIP payload alike (no ``aps``)."""
    expires = datetime.fromtimestamp(request.expires_at_ms / 1000, tz=UTC)
    return {
        "type": CALL_REQUEST,
        "id": request.request_id,
        "payload": {
            "id": request.request_id,
            "source": SOURCE,
            "session": {"host": host, "id": resident_id, "name": name},
            "text": request.text,
            "notificationId": request.notification_id,
            "priority": request.priority,
            "expiresAt": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


# -- the decider -------------------------------------------------------------


class CallRequester:
    """Delivers queued call requests to the owner's phone, once each."""

    def __init__(
        self,
        store: VoiceCallStore,
        registry: CallPresenceRegistry,
        config: VoiceCallsConfig,
        *,
        name: str,
        push: CallPushPort,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._store = store
        self._registry = registry
        self._config = config
        self._name = name
        self._push = push
        self._clock = clock

    async def run(self) -> None:
        """Look at queued requests until cancelled; a failed pass is logged and retried."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Call requests pass failed")
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def tick(self) -> None:
        """One pass: deliver what may go now, then push what the phone left unanswered."""
        for request in self._store.call_requests(state="queued"):
            await self._deliver(request)
        for request in self._store.call_requests(state="sent"):
            await self._escalate(request)

    def frame(self, request: CallRequest) -> dict[str, Any]:
        return call_request_frame(
            request, host=self._config.host, resident_id=self._config.resident_id, name=self._name
        )

    async def _deliver(self, request: CallRequest) -> None:
        now = self._clock()
        if now >= request.expires_at_ms:
            self._store.expire_call_request(request.request_id)
            logger.info("Call request %s expired undelivered", request.request_id)
            return
        frame = self.frame(request)
        presence = self._registry.fresh()
        if presence is not None and await presence.send(frame):
            self._store.mark_call_sent(
                request.request_id, delivery="socket", device_id=presence.device_id, at_ms=now
            )
            logger.info("Call request %s sent on the socket", request.request_id)
            return
        if request.push_tried:
            return  # pushed once already; it waits for a socket until it expires
        await self._ring(request, frame, now)

    async def _escalate(self, request: CallRequest) -> None:
        """A socket delivery the phone did not answer in time is pushed once, same id."""
        if request.delivery != "socket" or request.push_tried or request.outcome is not None:
            return
        now = self._clock()
        timeout_ms = int(self._config.result_timeout_seconds * 1000)
        if request.sent_at_ms is None or now - request.sent_at_ms < timeout_ms:
            return
        await self._ring(request, self.frame(request), now)

    async def _ring(self, request: CallRequest, frame: dict[str, Any], now: int) -> None:
        try:
            result = await self._push.ring(request_id=request.request_id, frame=frame)
            sent, reason = result.sent, result.reason
        except Exception as exc:
            sent, reason = False, f"push_failed: {type(exc).__name__}"
            logger.warning("Call request %s push failed: %s", request.request_id, reason)
        self._store.mark_call_pushed(request.request_id, sent=sent, reason=reason, at_ms=now)
        logger.info(
            "Call request %s %s (%s)",
            request.request_id,
            "pushed" if sent else "not pushed",
            reason or "ok",
        )


async def cancel(task: asyncio.Task[Any] | None) -> None:
    """Stop the requester's task and wait for it."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        _ = await task
