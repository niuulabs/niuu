"""Keep device-flow sign-in tokens alive by refreshing them before they expire.

GitLab user tokens live two hours and GitHub App tokens eight (when the app
issues expiring tokens); both come with a refresh token. This service walks
every connection whose definition signs in through the OAuth device grant,
refreshes the ones that are about to expire, and flips a connection to
"sign-in needed" the moment a refresh is rejected, so a session never has to
discover a dead token on its own.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from volundr.domain.models import IntegrationConnection, SecretType
from volundr.domain.ports import CredentialStorePort, IntegrationRepository
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.oauth_clients import OAuthClientRegistry

logger = logging.getLogger(__name__)

OAUTH_DEVICE_METHOD = "oauth_device"
DEFAULT_REFRESH_SKEW_SECONDS = 600
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
OAUTH_TOKEN_REFRESH_INTERVAL_SECONDS = 300.0
REFRESH_FAILED_ERROR_CODE = "refresh_failed"


@dataclass(frozen=True)
class RefreshReport:
    refreshed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class OAuthTokenRefreshService:
    """Refresh expiring device-flow tokens through each provider's token endpoint."""

    def __init__(
        self,
        *,
        integration_repository: IntegrationRepository,
        integration_registry: IntegrationRegistry,
        credential_store: CredentialStorePort,
        clients: OAuthClientRegistry,
        refresh_skew_seconds: int = DEFAULT_REFRESH_SKEW_SECONDS,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._repository = integration_repository
        self._registry = integration_registry
        self._store = credential_store
        self._clients = clients
        self._skew = timedelta(seconds=int(refresh_skew_seconds))
        self._timeout = float(request_timeout)

    async def refresh_due(self, now: datetime | None = None) -> RefreshReport:
        """Refresh every device-flow token that expires within the skew window."""
        now = now or datetime.now(UTC)
        report = RefreshReport()
        # Applications registered from the wizard land in the store; another
        # process (the integrations API) may have written them since last time.
        await self._clients.load()
        for connection in await self._repository.list_connections_global(enabled_only=True):
            definition = self._registry.get_definition(connection.slug)
            spec = definition.credential_enrollment if definition is not None else None
            if (
                definition is None
                or spec is None
                or spec.method != OAUTH_DEVICE_METHOD
                or definition.oauth is None
            ):
                continue
            values = await self._store.get_value(
                "user", connection.owner_id, connection.credential_name
            )
            if not values or not values.get("refresh_token"):
                continue
            expires_at = _parse(values.get("expires_at"))
            if expires_at is None or expires_at - now > self._skew:
                continue
            label = f"{connection.slug}/{connection.owner_id}/{connection.credential_name}"
            try:
                await self._refresh(connection, definition.oauth.token_url, values, now)
            except Exception as exc:
                logger.error("Token refresh for %s failed: %s", label, exc)
                await self._mark(
                    connection, state="auth_required", error_code=REFRESH_FAILED_ERROR_CODE
                )
                report.failed.append(label)
                continue
            report.refreshed.append(label)
        return report

    async def _refresh(
        self,
        connection: IntegrationConnection,
        token_url: str,
        values: dict[str, str],
        now: datetime,
    ) -> None:
        client = self._clients.get(connection.slug)
        if client is None:
            raise ValueError(
                f"no OAuth application is registered for {connection.slug}; register one from "
                "the setup wizard or set oauth.clients"
            )
        form = {
            "grant_type": "refresh_token",
            "refresh_token": values["refresh_token"],
            "client_id": client.client_id,
        }
        if client.client_secret:
            form["client_secret"] = client.client_secret
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                token_url, data=form, headers={"Accept": "application/json"}
            )
        data: dict[str, Any] = {}
        if "json" in response.headers.get("content-type", ""):
            parsed = response.json()
            data = parsed if isinstance(parsed, dict) else {}
        if response.status_code != 200 or "access_token" not in data:
            reason = data.get("error_description") or data.get("error") or response.text[:200]
            raise ValueError(f"provider answered HTTP {response.status_code}: {reason}")
        updated = dict(values)
        updated["token"] = str(data["access_token"])
        if data.get("refresh_token"):
            updated["refresh_token"] = str(data["refresh_token"])
        if data.get("expires_in"):
            updated["expires_at"] = (now + timedelta(seconds=float(data["expires_in"]))).isoformat()
        else:
            updated.pop("expires_at", None)
        await self._save(
            connection,
            updated,
            {
                "auth_state": "active",
                "auth_expires_at": updated.get("expires_at", ""),
                "auth_refreshed_at": now.isoformat(),
            },
            clear_error=True,
        )

    async def _mark(
        self, connection: IntegrationConnection, *, state: str, error_code: str
    ) -> None:
        values = await self._store.get_value(
            "user", connection.owner_id, connection.credential_name
        )
        await self._save(
            connection,
            dict(values or {}),
            {"auth_state": state, "auth_error_code": error_code},
            clear_error=False,
        )

    async def _save(
        self,
        connection: IntegrationConnection,
        values: dict[str, str],
        metadata_update: dict[str, str],
        *,
        clear_error: bool,
    ) -> None:
        stored = await self._store.get("user", connection.owner_id, connection.credential_name)
        metadata = dict(stored.metadata) if stored is not None else {}
        metadata.update(
            {k: v for k, v in metadata_update.items() if v != ""}
            | {"auth_state_updated_at": datetime.now(UTC).isoformat()}
        )
        if clear_error:
            metadata.pop("auth_error_code", None)
        await self._store.store(
            "user",
            connection.owner_id,
            connection.credential_name,
            stored.secret_type if stored is not None else SecretType.OAUTH_TOKEN,
            values,
            metadata,
        )


def _parse(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def refresh_oauth_tokens_loop(
    service: OAuthTokenRefreshService,
    *,
    interval_seconds: float = OAUTH_TOKEN_REFRESH_INTERVAL_SECONDS,
) -> None:
    """Refresh device-flow tokens on a timer, independently of any session."""
    logger.info("OAuth token refresh started, interval=%.0fs", interval_seconds)
    while True:
        try:
            report = await service.refresh_due()
            if report.refreshed or report.failed:
                logger.info(
                    "OAuth token refresh: %d refreshed, %d failed",
                    len(report.refreshed),
                    len(report.failed),
                )
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("OAuth token refresh task cancelled")
            break
        except Exception:
            logger.exception("OAuth token refresh iteration failed")
            await asyncio.sleep(interval_seconds)
