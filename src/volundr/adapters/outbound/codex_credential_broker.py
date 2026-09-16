"""Read OpenBao-managed Codex access tokens without owning provider refresh."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from niuu.domain.codex_credentials import (
    CODEX_AUTH_FORMAT,
    codex_plan_type,
    jwt_remaining_seconds,
    parse_codex_auth_document,
)
from niuu.domain.oauth_credentials import OAUTH_ENGINE, OAuthCredentialUnavailableError
from volundr.domain.ports import CodexAuthTokens, CodexCredentialBrokerPort, CredentialStorePort


class CodexCredentialBrokerError(ValueError):
    """Safe delivery failure; distinguish vault outages from reconnect requests."""

    def __init__(self, message: str, *, reconnect: bool = True):
        self.reconnect = reconnect
        super().__init__(message)


class DisabledCodexCredentialBroker(CodexCredentialBrokerPort):
    """Local-mode adapter that leaves Codex host authentication untouched."""

    def __init__(self, **_extra: object) -> None:
        pass

    async def get_tokens(
        self,
        *,
        owner_id: str,
        tenant_id: str,
        credential_name: str,
        credential_field: str,
        force_refresh: bool = False,
        previous_access_token_sha256: str = "",
    ) -> CodexAuthTokens:
        raise CodexCredentialBrokerError("Brokered Codex authentication is disabled")


class OpenBaoCodexCredentialBroker(CodexCredentialBrokerPort):
    """Authorize one user's grant and deliver its current access-only envelope."""

    def __init__(self, *, credential_store: CredentialStorePort, **_extra: object) -> None:
        self._credential_store = credential_store

    async def get_tokens(
        self,
        *,
        owner_id: str,
        tenant_id: str,
        credential_name: str,
        credential_field: str,
        force_refresh: bool = False,
        previous_access_token_sha256: str = "",
    ) -> CodexAuthTokens:
        if not owner_id or not tenant_id or not credential_name or not credential_field:
            raise CodexCredentialBrokerError("Codex credential reference is incomplete")
        stored = await self._credential_store.get("user", owner_id, credential_name)
        if stored is None or stored.metadata.get("tenant_id") != tenant_id:
            raise CodexCredentialBrokerError("Codex credential is unavailable for this caller")
        if (
            stored.metadata.get("renewal_owner") != OAUTH_ENGINE
            or stored.metadata.get("oauth_format") != CODEX_AUTH_FORMAT
            or stored.metadata.get("oauth_token_field") != credential_field
        ):
            raise CodexCredentialBrokerError(
                "Codex credential requires OpenBao migration or reconnection"
            )
        try:
            values = await self._credential_store.get_value("user", owner_id, credential_name)
            auth = parse_codex_auth_document(values.get(credential_field) if values else None)
            access_token = auth["tokens"]["access_token"]
            remaining = jwt_remaining_seconds(access_token)
            if values and values.get("expires_at"):
                expiry = datetime.fromisoformat(values["expires_at"])
                remaining = min(remaining, int((expiry - datetime.now(UTC)).total_seconds()))
        except OAuthCredentialUnavailableError as exc:
            raise CodexCredentialBrokerError(str(exc), reconnect=exc.reconnect) from None
        except (ValueError, TypeError):
            raise CodexCredentialBrokerError("Codex access token is unavailable") from None
        if remaining <= 0:
            raise CodexCredentialBrokerError(
                "OpenBao returned an expired Codex token", reconnect=False
            )
        # oauthapp owns expiry-driven renewal. It has no force-refresh endpoint.
        # Never return the same rejected token as if it had been renewed.
        if force_refresh and (
            not previous_access_token_sha256
            or hmac.compare_digest(
                hashlib.sha256(access_token.encode()).hexdigest(), previous_access_token_sha256
            )
        ):
            raise CodexCredentialBrokerError(
                "Codex rejected the current grant; reconnect the integration"
            )
        return CodexAuthTokens(
            access_token=access_token,
            account_id=auth["tokens"]["account_id"],
            expires_in=remaining,
            plan_type=codex_plan_type(auth, access_token),
        )
