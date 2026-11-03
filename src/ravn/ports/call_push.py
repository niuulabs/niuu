"""Port for ringing the owner's phone with a VoIP push when no socket can carry a call request.

A resident's voice gateway asks the owner's phone to call (``call.request``). While
the phone holds a fresh standby socket the frame goes over it; otherwise, or when
the phone does not answer on the socket in time, the gateway rings it through this
port with the same frame and the same request id. The phone handles an id once, so
a push that follows a socket delivery is reported as a duplicate and ends at once.

The resident holds no push key: an adapter asks the platform's push gateway to
ring the owner (``voice.calls.push``). Without one configured, :class:`NullCallPush`
answers that push is not configured and the notification's alert is the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CallPushResult:
    """What one ring achieved."""

    #: The push service accepted it for at least one token.
    sent: bool
    #: VoIP tokens tried.
    tokens: int
    #: Why nothing was sent, or a note on what was: ``push_not_configured``,
    #: ``no_voip_token``, ``rate_limited``, ``http_<status>``, …
    reason: str = ""


@runtime_checkable
class CallPushPort(Protocol):
    """Rings the owner's phone with one ``call.request`` frame (the push payload, no ``aps``)."""

    async def ring(self, *, request_id: str, frame: dict[str, Any]) -> CallPushResult:
        """Ring once for ``request_id``; every failure is a result with a reason, never raised."""


class NullCallPush:
    """The default when ``voice.calls.push`` is unset: nothing is pushed."""

    async def ring(self, *, request_id: str, frame: dict[str, Any]) -> CallPushResult:
        return CallPushResult(sent=False, tokens=0, reason="push_not_configured")
