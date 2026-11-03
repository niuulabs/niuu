"""Ring the owner's phone through the platform's push gateway (``CallPushPort``).

The resident holds no push key. It asks the Guild push gateway to ring its owner:
``POST <base_url>/api/v1/forge/push/call`` with ``{request_id, frame}``, the frame
being the ``call.request`` the phone also takes over its socket. The gateway sends it
as a VoIP push to the owner's registered PushKit tokens and answers
``{sent, tokens, results}``.

Auth is the pod's projected workload identity, exchanged for a short-lived bearer
with the scope ``forge:push:call``. The exchanged token speaks for the resident's
delegating owner and names the resident, so the gateway rings only that owner, and
only for a frame from this resident.

Configured as ``voice.calls.push``::

    adapter: ravn.adapters.voice.call_push.ForgeCallPushClient
    kwargs:
      base_url: https://yggdrasil.niuu.world
      exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
      token_file: /var/run/secrets/niuu-workload/token

A ring never raises: every failure is a :class:`CallPushResult` with a reason, and
the notification's alert stays the fallback. Logs carry request ids only, never a
token or the call's text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from niuu.adapters.outbound.http_auth import WorkloadIdentityBearerTokenAuthAdapter
from niuu.domain.services.token_scope import FORGE_PUSH_CALL_SCOPE
from niuu.ports.http_auth import HttpAuthPort
from ravn.ports.call_push import CallPushResult

logger = logging.getLogger(__name__)

#: The push gateway's ring route, under ``base_url``.
CALL_PATH = "/api/v1/forge/push/call"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_STATUS_OK = 200
_STATUS_UNAUTHORIZED = 401
_STATUS_TOO_MANY = 429


class ForgeCallPushClient:
    """Implements ``CallPushPort`` against the Guild push gateway."""

    def __init__(
        self,
        *,
        base_url: str,
        exchange_url: str = "",
        token_file: str = "",
        audiences: Sequence[str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        auth: HttpAuthPort | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        exchange_transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError(
                "voice.calls.push needs base_url: the origin of the Guild push gateway "
                "(e.g. https://yggdrasil.niuu.world)"
            )
        self._url = base_url.strip().rstrip("/") + CALL_PATH
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._auth = auth or WorkloadIdentityBearerTokenAuthAdapter(
            base_url=base_url.strip(),
            exchange_url=exchange_url,
            token_file=token_file,
            audiences=list(audiences or ["volundr-api"]),
            scopes=[FORGE_PUSH_CALL_SCOPE],
            timeout_seconds=timeout_seconds,
            transport=exchange_transport,
        )

    async def ring(self, *, request_id: str, frame: dict[str, Any]) -> CallPushResult:
        """Ask the gateway to ring the owner with ``frame``; a stale bearer is renewed once."""
        for attempt in range(2):
            try:
                # The exchange is a blocking call; keep it off the event loop.
                headers = await asyncio.to_thread(self._auth.headers)
            except Exception as exc:  # noqa: BLE001 - a ring never raises
                logger.warning(
                    "Call request %s not rung: no workload credential (%s)", request_id, exc
                )
                return CallPushResult(sent=False, tokens=0, reason="no_credential")
            if not headers:
                logger.warning(
                    "Call request %s not rung: no workload identity configured", request_id
                )
                return CallPushResult(sent=False, tokens=0, reason="no_credential")
            try:
                response = await self._post(request_id, frame, headers)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Call request %s not rung: push gateway unreachable (%s)", request_id, exc
                )
                return CallPushResult(sent=False, tokens=0, reason="unreachable")
            if response.status_code == _STATUS_UNAUTHORIZED and attempt == 0:
                if self._auth.invalidate():
                    continue
            return self._result(request_id, response)
        return CallPushResult(sent=False, tokens=0, reason=f"http_{_STATUS_UNAUTHORIZED}")

    async def _post(
        self, request_id: str, frame: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            return await client.post(
                self._url, json={"request_id": request_id, "frame": frame}, headers=headers
            )

    @staticmethod
    def _result(request_id: str, response: httpx.Response) -> CallPushResult:
        status = response.status_code
        if status == _STATUS_TOO_MANY:
            logger.info("Call request %s not rung: the owner's ring cap is full", request_id)
            return CallPushResult(sent=False, tokens=0, reason="rate_limited")
        if status != _STATUS_OK:
            logger.warning("Call request %s not rung: push gateway answered %d", request_id, status)
            return CallPushResult(sent=False, tokens=0, reason=f"http_{status}")
        try:
            body = response.json()
        except ValueError:
            return CallPushResult(sent=False, tokens=0, reason="bad_response")
        if not isinstance(body, dict):
            return CallPushResult(sent=False, tokens=0, reason="bad_response")
        sent = body.get("sent") is True
        tokens = body.get("tokens") if type(body.get("tokens")) is int else 0
        if sent:
            reason = ""
        elif tokens == 0:
            reason = "no_voip_token"
        else:
            reasons = sorted(
                {
                    str(item.get("reason") or item.get("status"))
                    for item in body.get("results") or []
                    if isinstance(item, dict)
                }
            )
            reason = "apns_refused" + (f": {', '.join(reasons)}" if reasons else "")
        logger.info(
            "Call request %s: %s (%d VoIP token(s))",
            request_id,
            "rung" if sent else reason,
            tokens,
        )
        return CallPushResult(sent=sent, tokens=tokens, reason=reason)
