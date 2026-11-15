"""Ask Forge for a call's short-lived realtime provider credential.

The provider key is the owner's and stays in Forge: the broker sends what the call needs
(provider, model, voice, instructions) with this session's own Forge credential (scope
``forge:session:voice``) and gets back a secret that expires within minutes. Every
refusal is an error with Forge's reason; the channel never tries another provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from niuu.domain.session_voice import voice_client_secrets_path


class VoiceMintError(RuntimeError):
    """Forge did not mint the credential; the message says why."""


@dataclass(frozen=True)
class MintedCredential:
    provider: str
    token: str
    expires_at_ms: int
    model: str
    #: Whether the instructions and voice are baked into the credential (OpenAI).
    session_bound: bool


class ForgeVoiceMinter:
    """Mint through Forge's ``POST /sessions/{id}/voice/client-secrets``."""

    def __init__(
        self,
        client: Callable[[], Awaitable[httpx.AsyncClient]],
        *,
        session_id: str,
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._path = voice_client_secrets_path(session_id)
        self._timeout_seconds = timeout_seconds

    async def mint(
        self, *, provider: str, model: str, voice: str, instructions: str, reasoning: str
    ) -> MintedCredential:
        client = await self._client()
        try:
            response = await client.post(
                self._path,
                json={
                    "provider": provider,
                    "model": model,
                    "voice": voice,
                    "instructions": instructions,
                    "reasoning": reasoning,
                },
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise VoiceMintError(
                f"Forge could not be reached to mint a {provider} credential: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise VoiceMintError(
                f"Forge refused to mint a {provider} voice credential "
                f"(HTTP {response.status_code}): {_detail(response)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise VoiceMintError("Forge answered the mint with a body that is not JSON") from exc
        try:
            return MintedCredential(
                provider=str(body["provider"]),
                token=_required_str(body, "token"),
                expires_at_ms=int(body["expires_at_ms"]),
                model=_required_str(body, "model"),
                session_bound=bool(body["session_bound"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VoiceMintError(
                f"Forge answered the mint without a usable credential: {exc}"
            ) from exc


def _required_str(body: dict, key: str) -> str:
    value = body[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is empty")
    return value


def _detail(response: httpx.Response) -> str:
    """Forge's own reason (its ``detail``), or the status line when it gave none."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or "no reason given"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    return response.reason_phrase or "no reason given"
