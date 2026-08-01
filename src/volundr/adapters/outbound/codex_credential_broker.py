"""Codex OAuth credential broker backed by the configured credential store."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt

from volundr.domain.ports import (
    CodexAuthTokens,
    CodexCredentialBrokerPort,
    CredentialRefreshLockPort,
    CredentialStorePort,
)

DEFAULT_CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CODEX_REFRESH_SKEW_SECONDS = 300
DEFAULT_CODEX_OAUTH_TIMEOUT_SECONDS = 15.0

CODEX_AUTH_STATE_METADATA_KEY = "auth_state"
CODEX_AUTH_ERROR_CODE_METADATA_KEY = "auth_error_code"
CODEX_AUTH_STATE_UPDATED_AT_METADATA_KEY = "auth_state_updated_at"
CODEX_AUTH_LAST_REFRESHED_AT_METADATA_KEY = "auth_last_refreshed_at"
CODEX_AUTH_STATE_ACTIVE = "active"
CODEX_AUTH_STATE_REQUIRED = "auth_required"
CODEX_AUTH_REFRESH_FAILED = "refresh_failed"


class CodexCredentialBrokerError(ValueError):
    """Safe failure raised when brokered Codex authentication is unavailable."""


class DisabledCodexCredentialBroker(CodexCredentialBrokerPort):
    """Configured local-mode adapter that leaves Codex host authentication untouched."""

    def __init__(self, **_extra: object) -> None:
        pass

    async def get_tokens(
        self,
        *,
        owner_id: str,
        credential_name: str,
        credential_field: str,
        force_refresh: bool = False,
        previous_access_token_sha256: str = "",
    ) -> CodexAuthTokens:
        del (
            owner_id,
            credential_name,
            credential_field,
            force_refresh,
            previous_access_token_sha256,
        )
        raise CodexCredentialBrokerError("Brokered Codex authentication is disabled")


class OpenBaoCodexCredentialBroker(CodexCredentialBrokerPort):
    """Keep refresh tokens in OpenBao and return access-only token envelopes."""

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort,
        refresh_lock: CredentialRefreshLockPort,
        oauth_token_url: str = DEFAULT_CODEX_OAUTH_TOKEN_URL,
        oauth_client_id: str = DEFAULT_CODEX_OAUTH_CLIENT_ID,
        refresh_skew_seconds: int = DEFAULT_CODEX_REFRESH_SKEW_SECONDS,
        oauth_timeout_seconds: float = DEFAULT_CODEX_OAUTH_TIMEOUT_SECONDS,
        oauth_client: httpx.AsyncClient | None = None,
        **_extra: object,
    ) -> None:
        self._credential_store = credential_store
        self._refresh_lock = refresh_lock
        self._oauth_token_url = oauth_token_url
        self._oauth_client_id = oauth_client_id
        self._refresh_skew_seconds = int(refresh_skew_seconds)
        self._oauth_timeout_seconds = float(oauth_timeout_seconds)
        self._oauth_client = oauth_client

    async def get_tokens(
        self,
        *,
        owner_id: str,
        credential_name: str,
        credential_field: str,
        force_refresh: bool = False,
        previous_access_token_sha256: str = "",
    ) -> CodexAuthTokens:
        if not owner_id or not credential_name or not credential_field:
            raise CodexCredentialBrokerError("Codex credential reference is incomplete")

        async with self._refresh_lock.hold("user", owner_id, credential_name):
            stored = await self._credential_store.get("user", owner_id, credential_name)
            values = await self._credential_store.get_value("user", owner_id, credential_name)
            raw_auth = values.get(credential_field) if values else None
            auth = parse_codex_auth_document(raw_auth)
            access_token = str(auth["tokens"]["access_token"])
            remaining = jwt_remaining_seconds(access_token)

            forced_refresh_is_still_current = force_refresh and (
                not previous_access_token_sha256
                or hmac.compare_digest(
                    hashlib.sha256(access_token.encode()).hexdigest(),
                    previous_access_token_sha256,
                )
            )
            if forced_refresh_is_still_current or remaining <= self._refresh_skew_seconds:
                try:
                    auth = await self._refresh(auth)
                except CodexCredentialBrokerError as exc:
                    if stored is not None and values is not None:
                        await self._credential_store.store(
                            "user",
                            owner_id,
                            credential_name,
                            stored.secret_type,
                            values,
                            codex_auth_metadata(
                                stored.metadata,
                                state=CODEX_AUTH_STATE_REQUIRED,
                                error_code=CODEX_AUTH_REFRESH_FAILED,
                            ),
                        )
                    raise CodexCredentialBrokerError(
                        "Codex authentication requires reconnection"
                    ) from exc

                if stored is None or values is None:
                    raise CodexCredentialBrokerError("Codex credential metadata is unavailable")
                access_token = str(auth["tokens"]["access_token"])
                remaining = jwt_remaining_seconds(access_token)
                updated_values = dict(values)
                updated_values[credential_field] = json.dumps(auth, separators=(",", ":"))
                await self._credential_store.store(
                    "user",
                    owner_id,
                    credential_name,
                    stored.secret_type,
                    updated_values,
                    codex_auth_metadata(
                        stored.metadata,
                        state=CODEX_AUTH_STATE_ACTIVE,
                        refreshed=True,
                    ),
                )

        tokens = auth["tokens"]
        return CodexAuthTokens(
            access_token=access_token,
            account_id=str(tokens["account_id"]),
            expires_in=max(remaining, 1),
            plan_type=_codex_plan_type(auth, access_token),
        )

    async def _refresh(self, auth: dict[str, Any]) -> dict[str, Any]:
        tokens = dict(auth["tokens"])
        refresh_token = str(tokens.get("refresh_token") or "")
        if not refresh_token:
            raise CodexCredentialBrokerError("Codex OAuth refresh token is unavailable")

        request_data = {
            "grant_type": "refresh_token",
            "client_id": self._oauth_client_id,
            "refresh_token": refresh_token,
        }
        try:
            if self._oauth_client is not None:
                response = await self._oauth_client.post(self._oauth_token_url, data=request_data)
            else:
                async with httpx.AsyncClient(timeout=self._oauth_timeout_seconds) as client:
                    response = await client.post(self._oauth_token_url, data=request_data)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CodexCredentialBrokerError("Codex OAuth token refresh failed") from exc

        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise CodexCredentialBrokerError("Codex OAuth token refresh returned no access token")
        tokens["access_token"] = access_token
        if payload.get("refresh_token"):
            tokens["refresh_token"] = str(payload["refresh_token"])
        if payload.get("id_token"):
            tokens["id_token"] = str(payload["id_token"])

        refreshed = dict(auth)
        refreshed["tokens"] = tokens
        refreshed["last_refresh"] = datetime.now(UTC).isoformat()
        return refreshed


def parse_codex_auth_document(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise CodexCredentialBrokerError("Codex auth document is unavailable")
    try:
        auth = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexCredentialBrokerError("Codex auth document is invalid JSON") from exc
    if not isinstance(auth, dict) or not isinstance(auth.get("tokens"), dict):
        raise CodexCredentialBrokerError("Codex auth document does not contain a token set")
    required = ("access_token", "refresh_token", "account_id")
    missing = [name for name in required if not str(auth["tokens"].get(name) or "")]
    if missing:
        raise CodexCredentialBrokerError("Codex auth document is missing required OAuth fields")
    return auth


def jwt_remaining_seconds(token: str) -> int:
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )
        expires_at = int(claims["exp"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise CodexCredentialBrokerError(
            "Codex OAuth access token is not a valid expiring JWT"
        ) from exc
    return expires_at - int(time.time())


def codex_auth_metadata(
    metadata: dict[str, Any],
    *,
    state: str,
    error_code: str = "",
    refreshed: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    updated = dict(metadata)
    updated[CODEX_AUTH_STATE_METADATA_KEY] = state
    updated[CODEX_AUTH_STATE_UPDATED_AT_METADATA_KEY] = now
    if error_code:
        updated[CODEX_AUTH_ERROR_CODE_METADATA_KEY] = error_code
    else:
        updated.pop(CODEX_AUTH_ERROR_CODE_METADATA_KEY, None)
    if refreshed:
        updated[CODEX_AUTH_LAST_REFRESHED_AT_METADATA_KEY] = now
    return updated


def _codex_plan_type(auth: dict[str, Any], access_token: str) -> str:
    explicit = str(auth.get("chatgpt_plan_type") or auth.get("plan_type") or "")
    if explicit:
        return explicit
    try:
        claims = jwt.decode(
            access_token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )
    except jwt.PyJWTError:
        return ""
    auth_claim = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        return ""
    return str(auth_claim.get("chatgpt_plan_type") or "")
