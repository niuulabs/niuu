"""Client for Völundr's Codex token broker.

A ChatGPT/Codex subscription credential is owned by a Völundr user. The broker
(``POST /api/v1/internal/credentials/codex/tokens``) refreshes it and hands out
short-lived, access-only tokens to the caller's principal, so no refresh token
ever leaves Völundr. This client authenticates with a configured
:class:`~niuu.ports.http_auth.HttpAuthPort` (normally the workload-identity
exchange of the caller's service account) and caches the token until shortly
before it expires.

It deliberately mirrors ``skuld.codex_auth.VolundrCodexAuthProvider`` (same
request and response shape) without importing it: Bifröst does not depend on
Skuld.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from bifrost.ports.provider import ProviderError
from niuu.ports.http_auth import HttpAuthPort

logger = logging.getLogger(__name__)

# Longest slice of a broker response body quoted in an error message.
_BODY_EXCERPT_LIMIT = 200


class CodexSubscriptionError(ProviderError):
    """The Codex subscription credential cannot be used; the message says how to fix it."""


@dataclass(frozen=True)
class CodexCredentials:
    """An access token for the ChatGPT backend, with the account it belongs to."""

    access_token: str
    account_id: str
    plan_type: str
    expires_at: float  # monotonic clock seconds; usable until then


class CodexTokenBroker:
    """Fetch and cache access tokens from the Völundr Codex token broker."""

    def __init__(
        self,
        *,
        base_url: str,
        auth: HttpAuthPort,
        credential_name: str,
        credential_field: str,
        token_path: str,
        timeout: float,
        refresh_skew_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{token_path.lstrip('/')}"
        self._auth = auth
        self._credential_name = credential_name
        self._credential_field = credential_field
        self._refresh_skew_seconds = refresh_skew_seconds
        self._clock = clock
        self._client = httpx.AsyncClient(timeout=timeout)
        self._lock = asyncio.Lock()
        self._cached: CodexCredentials | None = None

    async def credentials(self, *, rejected: CodexCredentials | None = None) -> CodexCredentials:
        """Return usable credentials.

        Args:
            rejected: Credentials the ChatGPT backend just refused. They are not
                served again: the broker is asked to refresh, unless a concurrent
                caller already replaced them.

        Raises:
            CodexSubscriptionError: The subscription must be reconnected in
                Völundr, or the broker cannot be reached or answered badly.
        """
        async with self._lock:
            cached = self._cached
            if rejected is None and cached is not None and self._clock() < cached.expires_at:
                return cached
            if rejected is not None and cached is not None:
                if cached.access_token != rejected.access_token:
                    return cached
            self._cached = await self._fetch(rejected)
            return self._cached

    async def close(self) -> None:
        await self._client.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        # The configured adapter may block (workload-identity exchange is a
        # synchronous HTTP call), so keep it off the event loop.
        headers = await asyncio.to_thread(self._auth.headers)
        if not headers:
            raise CodexSubscriptionError(
                "the Codex token broker needs authentication but the configured "
                "auth_adapter produced no headers: set auth_adapter/auth_kwargs on the "
                "provider (for example the workload-identity exchange of this "
                "service's account)"
            )
        return headers

    async def _send(self, body: dict[str, Any]) -> httpx.Response:
        return await self._client.post(self._url, json=body, headers=await self._auth_headers())

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        """POST to the broker; a rejected credential is re-minted once if the auth adapter can."""
        try:
            response = await self._send(body)
            rejected = response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN)
            if rejected and self._auth.invalidate():
                response = await self._send(body)
        except httpx.RequestError as exc:
            raise CodexSubscriptionError(
                f"the Codex token broker at {self._url} is unreachable "
                f"({type(exc).__name__}: {exc}); check broker_base_url and that Völundr is up"
            ) from exc
        return response

    async def _fetch(self, rejected: CodexCredentials | None) -> CodexCredentials:
        body = {
            "credential_name": self._credential_name,
            "credential_field": self._credential_field,
            "force_refresh": rejected is not None,
            "previous_access_token_sha256": (
                hashlib.sha256(rejected.access_token.encode()).hexdigest() if rejected else ""
            ),
        }
        response = await self._post(body)
        if response.status_code == httpx.codes.CONFLICT:
            raise CodexSubscriptionError(
                "Codex subscription needs reconnecting in Völundr: the broker reports the "
                f"stored ChatGPT credential cannot be refreshed ({_detail(response)}). "
                "Reconnect the Codex credential for the user this service account maps to, "
                "then retry."
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise CodexSubscriptionError(
                f"the Codex token broker answered {response.status_code} "
                f"({_detail(response)}); a 401/403 means this service account is not "
                "mapped to a user with a Codex credential, a 503 means the broker is down"
            )
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> CodexCredentials:
        try:
            payload = response.json()
            access_token = str(payload.get("access_token") or "")
            account_id = str(payload.get("chatgpt_account_id") or "")
            expires_in = float(payload["expires_in"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CodexSubscriptionError(
                "the Codex token broker returned a response without a usable "
                "access_token, chatgpt_account_id and expires_in"
            ) from exc
        if not access_token or not account_id:
            raise CodexSubscriptionError(
                "the Codex token broker returned an empty access_token or chatgpt_account_id"
            )
        return CodexCredentials(
            access_token=access_token,
            account_id=account_id,
            plan_type=str(payload.get("chatgpt_plan_type") or ""),
            expires_at=self._clock() + max(0.0, expires_in - self._refresh_skew_seconds),
        )


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else response.text[:_BODY_EXCERPT_LIMIT] or "no detail"
