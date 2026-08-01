"""Configured Codex authentication adapters for Skuld transports."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class CodexExternalTokens:
    access_token: str
    account_id: str
    plan_type: str = ""

    def app_server_payload(self) -> dict[str, str]:
        payload = {
            "accessToken": self.access_token,
            "chatgptAccountId": self.account_id,
        }
        if self.plan_type:
            payload["chatgptPlanType"] = self.plan_type
        return payload


class CodexAuthProviderError(RuntimeError):
    """Raised when externally managed Codex authentication cannot continue."""


class CodexAuthProviderPort(ABC):
    """Supply external ChatGPT tokens, or defer entirely to host Codex auth."""

    @abstractmethod
    async def get_tokens(self, *, force_refresh: bool = False) -> CodexExternalTokens | None:
        """Return external tokens, or None when Codex should use its host credential store."""


class HostCodexAuthProvider(CodexAuthProviderPort):
    """Non-Kubernetes adapter that preserves the existing host Codex login."""

    def __init__(self, **_extra: object) -> None:
        pass

    async def get_tokens(self, *, force_refresh: bool = False) -> None:
        del force_refresh
        return None


class VolundrCodexAuthProvider(CodexAuthProviderPort):
    """Fetch access-only tokens through Skuld's existing authenticated Volundr client."""

    def __init__(
        self,
        *,
        http_client_provider: Callable[[], Awaitable[httpx.AsyncClient]],
        credential_name: str = "codex-credentials",
        credential_field: str = "auth.json",
        token_path: str = "/api/v1/internal/credentials/codex/tokens",
        **_extra: object,
    ) -> None:
        self._http_client_provider = http_client_provider
        self._credential_name = credential_name
        self._credential_field = credential_field
        self._token_path = token_path
        self._last_access_token = ""

    async def get_tokens(
        self,
        *,
        force_refresh: bool = False,
    ) -> CodexExternalTokens:
        client = await self._http_client_provider()
        try:
            response = await client.post(
                self._token_path,
                json={
                    "credential_name": self._credential_name,
                    "credential_field": self._credential_field,
                    "force_refresh": force_refresh,
                    "previous_access_token_sha256": (
                        hashlib.sha256(self._last_access_token.encode()).hexdigest()
                        if force_refresh and self._last_access_token
                        else ""
                    ),
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CodexAuthProviderError(
                "Codex authentication requires reconnection"
            ) from exc

        access_token = str(payload.get("access_token") or "")
        account_id = str(payload.get("chatgpt_account_id") or "")
        if not access_token or not account_id:
            raise CodexAuthProviderError("Codex token broker returned an invalid response")
        self._last_access_token = access_token
        return CodexExternalTokens(
            access_token=access_token,
            account_id=account_id,
            plan_type=str(payload.get("chatgpt_plan_type") or ""),
        )
