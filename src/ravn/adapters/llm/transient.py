"""Surviving a model gateway that restarts under a model call.

Ravn's model calls go through Bifröst. When the platform rolls, the gateway pod
is stopped: a call made meanwhile meets a refused connection or a 5xx, and a
streamed answer it was in the middle of ends mid-body (``httpx.RemoteProtocolError:
peer closed connection without sending complete message body``). Without a
retry the whole task fails and its operator messages or commission are lost,
though the next request to the replacement pod would have worked.

Every failure of that kind, whether before the response header or after part of
the answer, is retried under one bound: a wall-clock window
(``llm.gateway_outage_window_seconds``), not an attempt count that stops
covering a rollout when the backoff changes. :class:`GatewayOutage` keeps it for
one model call. The window is the time the call may spend *failing*: from each
failure until the gateway answers again (a response header for a plain call, the
first event of the re-issued stream for a streamed one). A stream that then runs
for ten minutes does not use the window up, and one that is cut again straight
after delivering a few events does, so a gateway that flaps cannot keep a call
alive forever. When the window runs out the call raises an ``LLMError`` naming
the knob, and the exhaustion is counted on ``ravn.cap.hits``.

:func:`post_within_window` sends one request under that bound.
:func:`resumable_stream` additionally re-sends a streamed request whose answer
was cut, and tells the consumer to throw away the partial answer with a
``StreamEventType.STREAM_RESTART`` event. A model does not repeat itself word
for word, so a partial answer cannot be continued or de-duplicated, only
replaced.

Only transient failures are retried: a transport error, a status that says the
server was unavailable (:data:`RETRYABLE_STATUS_CODES`), or a stream that ended
without its terminator (:class:`StreamIncompleteError`). A 4xx other than 429, a
stream the provider ended as truncated, and a malformed answer are
deterministic; repeating the request would give the same result, so they raise.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import NoReturn

import httpx

from ravn.cap_telemetry import record_cap_hit, record_retry
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)

#: The knob that bounds every retry of a model call; named in every message about it.
GATEWAY_OUTAGE_KNOB = "llm.gateway_outage_window_seconds"

#: Statuses that mean the server could not take the request right now. 429 asks
#: the caller to come back; 500/502/503/504 are what a gateway or its upstream
#: answers while it is restarting or overloaded.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TransientStatusError(Exception):
    """A response whose status says the server was unavailable (retry later)."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class StreamIncompleteError(Exception):
    """A stream ended without the terminator its protocol ends every answer with.

    The server (or a gateway in front of it) closed the connection cleanly
    mid-answer, as a pod shutting down can. What arrived is a truncated answer,
    not an answer, and the request can be sent again.
    """


def describe_failure(exc: BaseException) -> str:
    """One-line cause of a transient failure, for logs and error messages."""
    if isinstance(exc, TransientStatusError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class GatewayOutage:
    """The retry bound of one model call: how long it may spend failing.

    ``failed()`` is called with each transient failure; it sleeps the backoff
    (``base_delay * 2**n`` seconds, ``n`` counting the failures since the gateway
    last answered, capped at *max_backoff_seconds* and never past the end of the
    window) or, when the window is spent, raises. ``recovered()`` is called when
    the gateway answers again.
    """

    def __init__(
        self,
        *,
        provider: str,
        window_seconds: float,
        base_delay: float,
        max_backoff_seconds: float,
        context: str = "",
    ) -> None:
        self._provider = provider
        self._window_seconds = window_seconds
        self._base_delay = base_delay
        self._max_backoff_seconds = max_backoff_seconds
        self._context = context
        self._down_since: float | None = None  # when the current run of failures began
        self._downtime = 0.0  # time spent failing in earlier runs of this call
        self._attempts = 0  # retries since the gateway last answered

    @property
    def path(self) -> str:
        return f"llm.{self._provider}"

    def recovered(self) -> None:
        """The gateway answered; stop the clock on the current run of failures."""
        if self._down_since is None:
            return
        outage = time.monotonic() - self._down_since
        self._downtime += outage
        logger.info(
            "%s: model call recovered after %d retry(ies) in %.1fs",
            self.path,
            self._attempts,
            outage,
        )
        self._down_since = None
        self._attempts = 0

    async def failed(self, failure: BaseException) -> None:
        """Wait before the next attempt, or raise ``LLMError`` if the window is spent."""
        now = time.monotonic()
        if self._down_since is None:
            self._down_since = now
        elapsed = self._downtime + (now - self._down_since)
        remaining = self._window_seconds - elapsed
        if remaining <= 0:
            self._exhausted(failure, elapsed)
        delay = min(self._base_delay * (2**self._attempts), self._max_backoff_seconds, remaining)
        self._attempts += 1
        record_retry(
            self.path,
            f"{self._provider} model call failed ({describe_failure(failure)}); retrying in "
            f"{delay:.1f}s (retry {self._attempts}, {elapsed:.1f}s into the "
            f"{self._window_seconds:g}s {GATEWAY_OUTAGE_KNOB} window)",
        )
        await asyncio.sleep(delay)

    def _exhausted(self, failure: BaseException, elapsed: float) -> NoReturn:
        cause = describe_failure(failure)
        status = failure.status_code if isinstance(failure, TransientStatusError) else None
        details = [self._context] if self._context else []
        if status is not None:
            details.append(f"status={status}")
        where = f" ({', '.join(details)})" if details else ""
        if self._window_seconds <= 0:
            message = (
                f"{self._provider} model call failed ({cause}){where} and {GATEWAY_OUTAGE_KNOB}=0 "
                f"disables retrying it; set {GATEWAY_OUTAGE_KNOB} to the longest gateway restart "
                "to survive"
            )
        else:
            message = (
                f"{self._provider} model call failed ({cause}){where} and did not recover "
                f"within {GATEWAY_OUTAGE_KNOB}={self._window_seconds:g}s ({self._attempts} "
                f"retry(ies), {elapsed:.1f}s spent failing); the gateway or provider has been "
                f"unreachable for longer than the window. Check it, or raise "
                f"{GATEWAY_OUTAGE_KNOB} to outlast the restart"
            )
        record_cap_hit(GATEWAY_OUTAGE_KNOB, message)
        raise LLMError(message, status_code=status) from failure


async def post_within_window(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    stream: bool,
    outage: GatewayOutage,
    headers: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> httpx.Response:
    """POST *payload*, retrying transient failures until *outage*'s window is spent.

    Returns the first response whose status is not in
    :data:`RETRYABLE_STATUS_CODES` (a 4xx is the caller's to judge). With
    *stream* the response is returned open. *headers*, when given, is awaited
    before every attempt so a retry minutes later carries a freshly minted
    credential instead of the one the client was created with.

    Raises:
        LLMError: The window ran out.
    """
    while True:
        request_headers = await headers() if headers is not None else None
        try:
            if stream:
                request = client.build_request("POST", url, json=payload, headers=request_headers)
                response = await client.send(request, stream=True)
            else:
                response = await client.post(url, json=payload, headers=request_headers)
        except httpx.TransportError as exc:
            await outage.failed(exc)
            continue
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response
        if stream:
            await response.aclose()
        await outage.failed(TransientStatusError(response.status_code))


async def resumable_stream(
    *,
    outage: GatewayOutage,
    open_response: Callable[[GatewayOutage], Awaitable[httpx.Response]],
    read_events: Callable[[httpx.Response], AsyncIterator[StreamEvent]],
) -> AsyncIterator[StreamEvent]:
    """Yield the events of one model call, re-sending it when the answer is cut.

    *open_response* sends the request (it is given *outage*, and normally calls
    :func:`post_within_window` with it) and returns the open streaming response.
    *read_events* parses one response into events; it raises ``LLMError`` for a
    deterministic failure and :class:`StreamIncompleteError` for an answer that ended
    without its terminator. A transport error or :class:`StreamIncompleteError` while
    reading sends the request again after the outage's backoff. When anything
    was already yielded from the failed attempt a ``STREAM_RESTART`` event is
    yielded first, so the consumer drops the partial answer.

    Raises:
        LLMError: The window ran out, naming ``llm.gateway_outage_window_seconds``.
    """
    delivered = False  # events yielded to the consumer since the last (re)start
    while True:
        response: httpx.Response | None = None
        failure: BaseException | None = None
        try:
            response = await open_response(outage)
            async for event in read_events(response):
                outage.recovered()
                delivered = True
                yield event
            return
        except (httpx.TransportError, StreamIncompleteError) as exc:
            failure = exc
        finally:
            if response is not None:
                await response.aclose()

        # The consumer is told before the backoff, so it stops showing a
        # partial answer that is not going to be completed.
        if delivered:
            yield StreamEvent(type=StreamEventType.STREAM_RESTART)
            delivered = False
        await outage.failed(failure)
