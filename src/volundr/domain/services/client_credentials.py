"""Mint short-lived access tokens from a service account's client credentials.

A team install of Jira is an Atlassian service account: the credential Forge
stores is its ``client_id`` / ``client_secret``, and what the Jira adapters send
is an access token minted from them with the OAuth ``client_credentials`` grant.
The minted token expires, so the refresh service re-mints it before then.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from volundr.domain.models import TeamInstallMethod, TeamInstallSpec

ClientFactory = Callable[[float], httpx.AsyncClient]

CLIENT_ID_FIELD = "client_id"
CLIENT_SECRET_FIELD = "client_secret"


class ClientCredentialsError(Exception):
    """A client-credentials token could not be minted; the message names the fix."""


def _default_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


class ClientCredentialsMinter:
    """Exchange ``client_id`` / ``client_secret`` for an access token."""

    def __init__(
        self,
        request_timeout: float,
        client_factory: ClientFactory = _default_client,
    ) -> None:
        self._timeout = float(request_timeout)
        self._client_factory = client_factory

    async def mint(self, spec: TeamInstallSpec, values: dict[str, str]) -> dict[str, str]:
        """Return ``values`` plus a fresh ``access_token`` and ``expires_at``."""
        if spec.method is not TeamInstallMethod.CLIENT_CREDENTIALS:
            raise ClientCredentialsError(
                f"team install method {spec.method.value!r} does not use client credentials"
            )
        if not spec.token_url:
            raise ClientCredentialsError(
                "the integration's team_install has no token_url; set team_install.token_url"
            )
        missing = [f for f in (CLIENT_ID_FIELD, CLIENT_SECRET_FIELD) if not values.get(f)]
        if missing:
            raise ClientCredentialsError(
                f"the service account credential is missing {', '.join(missing)}; "
                "re-enter the service account's client id and client secret"
            )
        body = {
            "grant_type": "client_credentials",
            CLIENT_ID_FIELD: values[CLIENT_ID_FIELD],
            CLIENT_SECRET_FIELD: values[CLIENT_SECRET_FIELD],
            **spec.token_params,
        }
        async with self._client_factory(self._timeout) as client:
            try:
                response = await client.post(
                    spec.token_url, json=body, headers={"Accept": "application/json"}
                )
            except httpx.HTTPError as exc:
                raise ClientCredentialsError(
                    f"the token endpoint {spec.token_url} could not be reached "
                    f"({type(exc).__name__}); check the integration's team_install.token_url"
                ) from exc
        data = _json_object(response)
        if response.status_code != 200:
            reason = data.get("error_description") or data.get("error") or "no reason given"
            raise ClientCredentialsError(
                f"the token endpoint answered HTTP {response.status_code} ({reason}); "
                "check the service account's client id and client secret"
            )
        access_token = data.get("access_token")
        if not access_token:
            raise ClientCredentialsError(
                "the token endpoint answered without an access_token; check the service "
                "account's client id, client secret and the integration's team_install.token_params"
            )
        minted = {**values, "access_token": str(access_token)}
        expires_in = data.get("expires_in")
        if not expires_in:
            raise ClientCredentialsError(
                "the token endpoint answered without expires_in, so the token could not be "
                "renewed in time; the provider must return expires_in"
            )
        minted["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=float(expires_in))
        ).isoformat()
        return minted


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
