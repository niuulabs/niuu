"""Prove who a ``forge_session`` credential speaks for by presenting it to Forge.

A service that is not Forge (Bifröst on its model routes) cannot verify a session
credential itself: whether the session is still live, on the same launch and not
stopped is Forge's to say. So it reads the session id off the credential
(unverified, only to address Forge) and presents the whole credential to
``GET {forge}/api/v1/forge/sessions/{id}``. Forge verifies signature, scope, binding
and the live session row; its 200 *is* the proof, and its answer is the identity
(owner, tenant, session) the caller acts as. Nothing the caller sent is trusted for
that, and nothing is cached, so a stopped session loses access with its next request.

Two ways to fail, kept apart because callers answer them differently:

* :class:`SessionRefusedError` — Forge answered and said no (or answered something
  that is not a session). The credential is not good: 401.
* :class:`ForgeUnavailableError` — Forge could not be asked. Nothing is known about
  the credential and nothing is assumed: 503.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM
from niuu.forge_mcp.credentials import is_forge_session_claims, unverified_claims

_SESSION_PATH = "/api/v1/forge/sessions/{session_id}"


class SessionNotConfirmedError(Exception):
    """Forge did not confirm the credential. The message names why and is safe to show."""


class SessionRefusedError(SessionNotConfirmedError):
    """Forge answered, and the credential is not a good one for a live session."""


class ForgeUnavailableError(SessionNotConfirmedError):
    """Forge could not be asked, so the credential is neither confirmed nor refused."""


@dataclass(frozen=True)
class ConfirmedSession:
    """What Forge said about the session a credential is bound to."""

    session_id: str
    owner_id: str
    tenant_id: str


def session_id_of(credential: str) -> str:
    """The session id a ``forge_session`` credential names (unverified, routing only).

    Raises :class:`SessionRefusedError` when the credential is not a session credential or
    names no valid session id; never use the result without Forge's confirmation.
    """
    claims = unverified_claims(credential)
    if not is_forge_session_claims(claims):
        raise SessionRefusedError("the credential is not a Forge session credential")
    raw = claims.get(SESSION_ID_CLAIM) if claims else None
    try:
        return str(UUID(str(raw)))
    except ValueError as exc:
        raise SessionRefusedError(
            f"the Forge session credential names no valid session ({SESSION_ID_CLAIM}={raw!r})"
        ) from exc


class ForgeSessionVerifier:
    """Confirms session credentials with Forge's own session route.

    Args:
        base_url: Forge's base URL, for example ``http://volundr:8080``.
        timeout_seconds: per-request timeout.
        transport: an ``httpx`` transport, for tests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("the Forge base URL is empty: set it to Forge's base URL")
        if timeout_seconds <= 0:
            raise ValueError("the Forge timeout must be positive")
        self._base = base_url.strip().rstrip("/")
        self._timeout = float(timeout_seconds)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def confirm(self, credential: str) -> ConfirmedSession:
        """Present ``credential`` to Forge and return who Forge says it is for."""
        session_id = session_id_of(credential)
        url = self._base + _SESSION_PATH.format(session_id=session_id)
        try:
            response = await self._http().get(
                url,
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ForgeUnavailableError(
                f"Forge could not be asked about session {session_id} at {self._base}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 500:
            raise ForgeUnavailableError(
                f"Forge at {self._base} answered {response.status_code} when asked about "
                f"session {session_id}"
            )
        if response.status_code != 200:
            raise SessionRefusedError(
                f"Forge refused the credential for session {session_id} "
                f"(answered {response.status_code}): it is not valid for a live session"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SessionRefusedError(
                f"Forge's answer for session {session_id} is not JSON"
            ) from exc
        return _confirmed(session_id, body)


def _confirmed(session_id: str, body: Any) -> ConfirmedSession:
    if not isinstance(body, dict):
        raise SessionRefusedError(f"Forge's answer for session {session_id} is not an object")
    answered = str(body.get("id") or "")
    if answered.lower() != session_id.lower():
        raise SessionRefusedError(
            f"Forge answered for session {answered!r}, not the credential's {session_id!r}"
        )
    for key in ("owner_id", "tenant_id"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SessionRefusedError(f"Forge's answer for session {session_id} has no {key}")
    return ConfirmedSession(
        session_id=session_id,
        owner_id=body["owner_id"],
        tenant_id=body["tenant_id"],
    )
