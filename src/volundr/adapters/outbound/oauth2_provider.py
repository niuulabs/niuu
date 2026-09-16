"""Generic OAuth2 provider utility — parameterized by OAuthSpec."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from volundr.domain.models import OAuthSpec

logger = logging.getLogger(__name__)


class OAuth2Provider:
    """Concrete OAuth2 helper, parameterized by URLs/scopes from config.

    One instance per integration slug. Not an ABC — there is nothing
    to subclass because behaviour is fully driven by ``OAuthSpec``.
    """

    def __init__(
        self,
        spec: OAuthSpec,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._spec = spec
        self._client_id = client_id
        self._client_secret = client_secret

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Build the provider's authorize URL."""
        params: dict[str, str] = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self._spec.scopes:
            params["scope"] = " ".join(self._spec.scopes)
        params.update(self._spec.extra_authorize_params)
        return f"{self._spec.authorize_url}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
    ) -> dict[str, str]:
        """Exchange an authorization code for credentials.

        Returns a dict of credential fields, applying
        ``token_field_mapping`` from the spec so the caller
        gets the names expected by the integration's
        ``credential_schema`` (e.g. ``{"api_key": "<token>"}``).
        """
        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        payload.update(self._spec.extra_token_params)

        async with httpx.AsyncClient() as client:
            request_body = (
                {"json": payload}
                if self._spec.token_request_format == "json"
                else {"data": payload}
            )
            resp = await client.post(
                self._spec.token_url,
                **request_body,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            token_data = resp.json()
        if not isinstance(token_data, dict) or not token_data.get("access_token"):
            raise ValueError("OAuth token response did not include an access token")

        result: dict[str, str] = {}
        for cred_field, token_field in self._spec.token_field_mapping.items():
            value = token_data.get(token_field, "")
            if value:
                result[cred_field] = value

        if not result:
            result["access_token"] = str(token_data["access_token"])

        mapped_token_fields = set(self._spec.token_field_mapping.values())
        refresh_token = token_data.get("refresh_token")
        if refresh_token and "refresh_token" not in mapped_token_fields:
            result["refresh_token"] = str(refresh_token)
        if token_data.get("expires_in"):
            expires_at = datetime.now(UTC) + timedelta(seconds=float(token_data["expires_in"]))
            result["expires_at"] = expires_at.isoformat()

        return result

    async def revoke_token(self, access_token: str) -> None:
        """Revoke an access token (best-effort, no-op if revoke_url is empty)."""
        if not self._spec.revoke_url:
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    self._spec.revoke_url,
                    data={"token": access_token},
                    headers={"Accept": "application/json"},
                )
        except Exception:
            logger.warning("Token revocation failed (best-effort)", exc_info=True)

    @staticmethod
    def generate_state() -> str:
        """Generate a cryptographically secure state parameter."""
        return secrets.token_urlsafe(32)
