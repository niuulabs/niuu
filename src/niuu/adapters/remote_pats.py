"""Central PAT management and revocation adapters for multi-service deployments."""

import math
import ssl
from datetime import datetime
from uuid import UUID

import httpx
from pydantic import BaseModel

from identity.adapters.remote import RemoteHeaderAuthenticationAdapter
from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.adapters.inbound.auth_context import current_bearer_token
from niuu.domain.models import PersonalAccessToken, Principal
from niuu.domain.services.pat_validator import PATValidator
from niuu.domain.services.token_scope import validate_pat_scopes
from niuu.ports.identity import InvalidTokenError


class _PATMetadata(BaseModel):
    id: UUID
    owner_id: str
    tenant_id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] | None = None


class RemotePATService:
    """Use one authority for issuance, enumeration and deletion across services."""

    def __init__(
        self, *, authority_url: str, timeout: float = 5, ca_file: str | None = None, **_extra
    ):
        if not authority_url.startswith("https://") or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("PAT authority requires HTTPS and a positive timeout")
        self._url = authority_url.rstrip("/") + "/api/v1/tokens"
        self._timeout = timeout
        self._tls = ssl.create_default_context(cafile=ca_file)

    async def _request(self, method: str, path: str = "", *, token: str = "", body=None):
        token = token or current_bearer_token() or ""
        if not token:
            raise AuthorizationDeniedError("The original bearer credential is required")
        try:
            async with httpx.AsyncClient(verify=self._tls, timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    self._url + path,
                    headers={"authorization": f"Bearer {token}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise AuthorizationEvaluationError("PAT authority unavailable") from exc
        if response.status_code in (401, 403):
            raise AuthorizationDeniedError("PAT authority denied the operation")
        if response.status_code not in (200, 201, 204, 404):
            raise AuthorizationEvaluationError("PAT authority unavailable")
        return response

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError as exc:
            raise AuthorizationEvaluationError("Invalid PAT authority response") from exc

    @staticmethod
    def _metadata(data, principal: Principal) -> PersonalAccessToken:
        try:
            record = _PATMetadata.model_validate(data)
        except ValueError as exc:
            raise AuthorizationEvaluationError("Invalid PAT authority response") from exc
        if record.owner_id != principal.user_id or record.tenant_id != principal.tenant_id:
            raise AuthorizationDeniedError("PAT authority returned a different owner or tenant")
        return PersonalAccessToken(**record.model_dump())

    async def create(
        self, principal: Principal, name: str, *, subject_token: str = "", scopes=None
    ):
        requested = validate_pat_scopes(scopes)
        response = await self._request(
            "POST", token=subject_token, body={"name": name, "scopes": scopes}
        )
        if response.status_code != 201:
            raise AuthorizationEvaluationError("PAT authority did not create the token")
        body = self._json(response)
        pat = self._metadata(body, principal)
        if requested is not None and pat.scopes != requested:
            raise AuthorizationEvaluationError("PAT authority expanded or omitted requested scopes")
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise AuthorizationEvaluationError("PAT authority returned no credential")
        return pat, token

    async def list(self, principal: Principal) -> list[PersonalAccessToken]:
        response = await self._request("GET")
        if response.status_code != 200 or not isinstance(self._json(response), list):
            raise AuthorizationEvaluationError("PAT authority returned invalid metadata")
        return [self._metadata(p, principal) for p in self._json(response)]

    async def revoke(self, pat_id: UUID, principal: Principal) -> bool:
        response = await self._request("DELETE", f"/{pat_id}")
        return response.status_code == 204


class RemotePATValidator(PATValidator):
    """Revalidate against the central authority, with no positive local cache."""

    def __init__(
        self, *, authority_url: str, timeout: float = 5, ca_file: str | None = None, **kwargs
    ):
        super().__init__(**kwargs)
        self._authority = RemoteHeaderAuthenticationAdapter(
            authority_url=authority_url, timeout=timeout, ca_file=ca_file
        )

    async def is_valid(self, raw_token: str) -> bool:
        try:
            await self._authority.validate_headers({"authorization": f"Bearer {raw_token}"})
        except InvalidTokenError:
            return False
        return True
