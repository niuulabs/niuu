"""Keycloak Token Exchange adapter for IDP-delegated token issuance.

Uses the OAuth 2.0 Token Exchange grant (RFC 8693) to obtain a
Keycloak-signed JWT scoped to a specific user. The resulting token
is recognised by Envoy's JWT filter because it shares the same
issuer and signing keys as regular OIDC tokens.

Requires a confidential client in Keycloak with:
  - ``token-exchange`` protocol mapper (or fine-grained permissions)
  - Access to the target audience
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt as pyjwt

from niuu.ports.token_issuer import IssuedToken, TokenIssuer

logger = logging.getLogger(__name__)

# Standard Token Exchange grant type (RFC 8693)
_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


class KeycloakTokenIssuer(TokenIssuer):
    """Issue long-lived PATs via Keycloak Token Exchange.

    Constructor kwargs (passed from config):
        token_url:   Keycloak token endpoint
                     (e.g. https://keycloak.example.com/realms/volundr/protocol/openid-connect/token)
        client_id:   Confidential client ID
        client_secret: Client secret
        audience:    Target audience for the issued token (e.g. "volundr-api")
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        audience: str = "",
        **_extra: object,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def issue_token(
        self,
        *,
        subject_token: str,
        name: str,
        ttl_days: int = 365,
    ) -> IssuedToken:
        """Exchange the user's access token for a long-lived PAT.

        The dedicated exchange client must map ``type: pat`` into the signed
        access token. Without that marker, the revocation validator cannot
        distinguish the credential from an ordinary interactive token.
        """
        client = await self._get_client()

        data: dict[str, Any] = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "subject_token": subject_token,
            "subject_token_type": _ACCESS_TOKEN_TYPE,
            "requested_token_type": _ACCESS_TOKEN_TYPE,
        }
        if self._audience:
            data["audience"] = self._audience

        logger.info(
            "Requesting token exchange: client=%s, audience=%s",
            self._client_id,
            self._audience,
        )

        resp = await client.post(self._token_url, data=data)

        if resp.status_code != 200:
            logger.error("Token exchange failed: status=%d", resp.status_code)
            raise RuntimeError(f"Token exchange failed (HTTP {resp.status_code})")

        body = resp.json()
        raw_token = body["access_token"]

        # Decode without verification — we trust Keycloak signed it
        claims = pyjwt.decode(raw_token, options={"verify_signature": False})

        if claims.get("type") != "pat":
            raise RuntimeError(
                "Exchanged token is not revocable: configure the dedicated PAT "
                "exchange client's signed type=pat claim"
            )
        subject = claims.get("sub")
        token_id = claims.get("jti")
        expiry = claims.get("exp")
        if not isinstance(subject, str) or not subject.strip():
            raise RuntimeError("Exchanged PAT has no subject")
        if not isinstance(token_id, str) or not token_id.strip():
            raise RuntimeError("Exchanged PAT has no token identifier")
        if type(expiry) is not int or expiry <= time.time():
            raise RuntimeError("Exchanged PAT has no valid expiry")
        return IssuedToken(
            raw_token=raw_token,
            token_id=token_id,
            subject=subject,
            expires_at=expiry,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
