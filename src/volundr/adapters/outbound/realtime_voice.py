"""Realtime credential minters over the providers' ``/v1/realtime/client_secrets``.

OpenAI and xAI both mint a short-lived secret at ``POST /v1/realtime/client_secrets``
with the account's API key. They differ in what the secret may carry:

* **OpenAI** scopes the secret to one realtime session: model, instructions, voice and
  reasoning effort are baked in, so the device cannot drift from them.
* **xAI** accepts only the expiry (it rejects a ``session`` object), so the secret is
  not scoped to a model. The device names the model on its own socket URL and applies
  the instructions itself.

The API key is passed per call: the owner's key comes from Forge's credential store when
the call is made and is never held by the minter.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

import httpx

from niuu.cap_telemetry import record_cap_hit
from volundr.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)

_CLIENT_SECRETS_PATH = "/v1/realtime/client_secrets"
_MS_PER_SECOND = 1000
#: OpenAI reasoning efforts a realtime session accepts; any other value is refused.
_OPENAI_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})


def _provider_message(response: httpx.Response) -> str:
    """The provider's own ``error.message`` from a JSON error body, or ``""``."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return message if isinstance(message, str) else ""
    return ""


class _ClientSecretsMinter(RealtimeCredentialMinterPort):
    """Shared request and response handling for the client_secrets endpoint."""

    #: Whether the secret carries the session's model, voice and instructions.
    session_bound: bool

    def __init__(
        self,
        *,
        base_url: str,
        ttl_seconds: int,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(
                f"{self.provider} realtime minter ttl_seconds must be positive, got {ttl_seconds}"
            )
        if timeout_seconds <= 0:
            raise ValueError(
                f"{self.provider} realtime minter timeout_seconds must be positive, "
                f"got {timeout_seconds}"
            )
        if not base_url:
            raise ValueError(f"{self.provider} realtime minter needs a base_url")
        self._url = base_url.rstrip("/") + _CLIENT_SECRETS_PATH
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @abstractmethod
    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        """The provider's request body for this call."""

    async def mint(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        reasoning: str = "",
    ) -> RealtimeCredential:
        body = self._body(model=model, voice=voice, instructions=instructions, reasoning=reasoning)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.TimeoutException as exc:
            knob = "voice.providers.<name>.kwargs.timeout_seconds"
            record_cap_hit(
                knob,
                f"{self.provider} realtime mint did not answer within "
                f"{self._timeout_seconds}s; raise {knob} or check the provider's reachability",
            )
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint timed out after {self._timeout_seconds}s; "
                f"raise {knob}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint failed: {type(exc).__name__}: {exc}; "
                "check the provider's base_url and its reachability"
            ) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            message = _provider_message(response)
            detail = f": {message}" if message else ""
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint returned HTTP {response.status_code}{detail}"
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
        # The secret is documented either at the top level or under ``client_secret``
        # (the older session-create shape).
        nested = data["client_secret"] if isinstance(data.get("client_secret"), dict) else {}
        token = data.get("value") or nested.get("value")
        if not isinstance(token, str) or not token:
            raise RealtimeCredentialError(f"{self.provider} realtime mint returned no secret")
        expires_at = data.get("expires_at") or nested.get("expires_at")
        if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
            raise RealtimeCredentialError(
                f"{self.provider} realtime mint returned a secret with no expiry; "
                "Forge will not guess when it stops working"
            )
        session = data["session"] if isinstance(data.get("session"), dict) else {}
        model = session.get("model")
        return RealtimeCredential(
            token=token,
            expires_at_ms=int(float(expires_at) * _MS_PER_SECOND),
            model=model if isinstance(model, str) and model else requested_model,
            session_bound=self.session_bound,
        )


class XaiRealtimeMinter(_ClientSecretsMinter):
    """Grok Voice: a TTL-only secret; the device applies model, voice and instructions."""

    provider = "xai"
    session_bound = False

    def __init__(
        self,
        *,
        base_url: str = "https://api.x.ai",
        ttl_seconds: int = 600,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            ttl_seconds=ttl_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        return {"expires_after": {"seconds": self._ttl_seconds}}


class OpenAIRealtimeMinter(_ClientSecretsMinter):
    """OpenAI Realtime: a session-scoped secret with model, instructions and voice baked in."""

    provider = "openai"
    session_bound = True

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com",
        ttl_seconds: int = 600,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            ttl_seconds=ttl_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def _body(self, *, model: str, voice: str, instructions: str, reasoning: str) -> dict[str, Any]:
        session: dict[str, Any] = {"type": "realtime", "model": model}
        if instructions:
            session["instructions"] = instructions
        if voice:
            session["audio"] = {"output": {"voice": voice}}
        if reasoning and reasoning not in _OPENAI_REASONING_EFFORTS:
            raise RealtimeCredentialError(
                f"openai realtime does not accept reasoning {reasoning!r}; use one of "
                f"{sorted(_OPENAI_REASONING_EFFORTS)} or leave it empty"
            )
        if reasoning:
            session["reasoning"] = {"effort": reasoning}
        return {
            "expires_after": {"anchor": "created_at", "seconds": self._ttl_seconds},
            "session": session,
        }
