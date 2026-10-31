"""Realtime credential minters over the providers' ``/v1/realtime/client_secrets``.

OpenAI and xAI both mint a short-lived secret at ``POST /v1/realtime/client_secrets``
with the account's API key. They differ in what the secret may carry:

* **OpenAI** scopes the secret to one realtime session: model, instructions,
  voice and reasoning effort are baked in, so the device cannot drift from them.
* **xAI** accepts only the expiry (it rejects a ``session`` object), so the secret
  is not scoped to a model. The device names the model on its own socket URL and
  applies the instructions itself.

The key is read from a mounted file (the OpenBao agent renders the owner's
credential there). A missing or empty key file is fatal at construction.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from ravn.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)

_CLIENT_SECRETS_PATH = "/v1/realtime/client_secrets"
#: Longest slice of a provider error body quoted in an exception.
_BODY_EXCERPT_LIMIT = 300
#: OpenAI reasoning efforts a realtime session accepts; anything else is not sent.
_OPENAI_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})


def _read_key(key_file: str, provider: str) -> str:
    if not key_file:
        raise ValueError(
            f"{provider} realtime minter needs key_file: the path the provider key is mounted at"
        )
    path = Path(key_file).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            f"{provider} realtime key file {str(path)!r} does not exist — is the owner's "
            f"{provider} credential mounted into the pod?"
        )
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError(f"{provider} realtime key file {str(path)!r} is empty")
    return key


class _ClientSecretsMinter(RealtimeCredentialMinterPort):
    """Shared request and response handling for the client_secrets endpoint."""

    provider = ""

    def __init__(
        self,
        *,
        key_file: str,
        base_url: str,
        ttl_seconds: int,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"{self.provider} realtime minter ttl_seconds must be positive")
        self._key = _read_key(key_file, self.provider)
        self._url = base_url.rstrip("/") + _CLIENT_SECRETS_PATH
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def _session_bound(self) -> bool:
        raise NotImplementedError

    async def mint(
        self,
        *,
        model: str,
        voice: str,
        instructions: str,
        reasoning: str = "",
    ) -> RealtimeCredential:
        body = self._body(model=model, voice=voice, instructions=instructions, reasoning=reasoning)
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            try:
                response = await client.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {self._key}"}
                )
            except httpx.HTTPError as exc:
                raise RealtimeCredentialError(
                    f"{self.provider} realtime mint failed: {exc}"
                ) from exc
        if response.status_code >= 400:
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint returned HTTP {response.status_code}: "
                f"{response.text[:_BODY_EXCERPT_LIMIT]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint returned a body that is not JSON"
            ) from exc
        return self._credential(data, model)

    def _credential(self, data: Any, requested_model: str) -> RealtimeCredential:
        if not isinstance(data, dict):
            raise RealtimeCredentialError(f"{self.provider} realtime mint returned no object")
        # Both providers document the secret either at the top level or under
        # ``client_secret`` (the older session-create shape).
        nested = data.get("client_secret") if isinstance(data.get("client_secret"), dict) else {}
        token = str(data.get("value") or nested.get("value") or "")
        expires_at = data.get("expires_at") or nested.get("expires_at")
        if not token:
            raise RealtimeCredentialError(f"{self.provider} realtime mint returned no secret")
        if not isinstance(expires_at, int | float):
            # No expiry in the answer: the secret lives for the TTL this request asked for.
            expires_at = time.time() + self._ttl_seconds
        session = data.get("session") if isinstance(data.get("session"), dict) else {}
        model = str(session.get("model") or requested_model)
        return RealtimeCredential(
            token=token,
            expires_at_ms=int(float(expires_at) * 1000),
            model=model,
            session_bound=self._session_bound,
        )


class XaiRealtimeMinter(_ClientSecretsMinter):
    """Grok Voice: a TTL-only secret; the device applies model, voice and instructions."""

    provider = "xai"

    def __init__(
        self,
        *,
        key_file: str,
        base_url: str = "https://api.x.ai",
        ttl_seconds: int = 600,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            key_file=key_file,
            base_url=base_url,
            ttl_seconds=ttl_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @property
    def _session_bound(self) -> bool:
        return False

    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        del model, voice, instructions, reasoning
        return {"expires_after": {"seconds": self._ttl_seconds}}


class OpenAIRealtimeMinter(_ClientSecretsMinter):
    """OpenAI Realtime: a session-scoped secret with model, instructions and voice baked in."""

    provider = "openai"

    def __init__(
        self,
        *,
        key_file: str,
        base_url: str = "https://api.openai.com",
        ttl_seconds: int = 600,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            key_file=key_file,
            base_url=base_url,
            ttl_seconds=ttl_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @property
    def _session_bound(self) -> bool:
        return True

    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        session: dict[str, Any] = {"type": "realtime", "model": model}
        if instructions:
            session["instructions"] = instructions
        if voice:
            session["audio"] = {"output": {"voice": voice}}
        if reasoning in _OPENAI_REASONING_EFFORTS:
            session["reasoning"] = {"effort": reasoning}
        return {
            "expires_after": {"anchor": "created_at", "seconds": self._ttl_seconds},
            "session": session,
        }
