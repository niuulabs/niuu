"""Resolve current identity and credential revocation at the shared identity API."""

import math

import httpx
from pydantic import BaseModel, Field, ValidationError

from identity.adapters.identity import EnvoyHeaderIdentityAdapter
from identity.models import Principal
from identity.ports import AuthorizationEvaluationError
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError


class _IdentityResponse(BaseModel):
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    email: str = ""
    roles: list[str]
    status: str


class RemoteHeaderAuthenticationAdapter(HeaderAuthenticationPort):
    """Forward the original credential to a TLS-verified identity authority.

    No positive cache: account, membership and PAT revocations are checked on
    every validation. No caller-supplied identity headers are sent upstream.
    """

    def __init__(
        self,
        *,
        authority_url: str,
        timeout: float = 5,
        ca_file: str | None = None,
        user_id_header: str = "x-auth-user-id",
        tenant_header: str = "x-auth-tenant",
        **_extra,
    ):
        if not authority_url.startswith("https://"):
            raise ValueError("Identity authority_url must use HTTPS")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Identity authority timeout must be positive")
        self._url = authority_url.rstrip("/") + "/api/v1/identity/me"
        self._timeout = timeout
        self._user_header = user_id_header.lower()
        self._tenant_header = tenant_header.lower()
        import ssl

        self._tls = ssl.create_default_context(cafile=ca_file)

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            raise InvalidTokenError("The original bearer credential is required")
        try:
            async with httpx.AsyncClient(verify=self._tls, timeout=self._timeout) as client:
                response = await client.get(self._url, headers={"authorization": auth})
        except httpx.HTTPError as exc:
            raise AuthorizationEvaluationError("Identity authority unavailable") from exc
        if response.status_code in (401, 403):
            raise InvalidTokenError("Identity or credential has been revoked")
        if response.status_code != 200:
            raise AuthorizationEvaluationError("Identity authority unavailable")
        try:
            identity = _IdentityResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AuthorizationEvaluationError("Invalid identity authority response") from exc
        if identity.status != "active":
            raise InvalidTokenError("Account is not active")
        for name, actual in [
            (self._user_header, identity.user_id),
            (self._tenant_header, identity.tenant_id),
        ]:
            if headers.get(name) and headers[name] != actual:
                raise InvalidTokenError("Identity authority disagrees with verified credential")
        return Principal(identity.user_id, identity.email, identity.tenant_id, identity.roles)


class RemoteIdentityAdapter(EnvoyHeaderIdentityAdapter):
    """Central authority with the existing local user-provisioning lifecycle.

    Local rows support service foreign keys and storage provisioning; they never
    establish membership authority. Every request is validated remotely first.
    """

    def __init__(
        self, *, authority_url: str, timeout: float = 5, ca_file: str | None = None, **kwargs
    ):
        kwargs.pop("membership_authority", None)
        super().__init__(membership_authority="idp", **kwargs)
        self._authority = RemoteHeaderAuthenticationAdapter(
            authority_url=authority_url, timeout=timeout, ca_file=ca_file, **kwargs
        )

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        return await self._authority.validate_headers(headers)
