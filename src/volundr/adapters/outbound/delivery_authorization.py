"""Campaign authorization through Ting's authenticated execution projection."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from niuu.domain.models import Principal
from niuu.ports.delivery import DeliveryAuthorizer


class HttpDeliveryAuthorizer(DeliveryAuthorizer):
    """Ask the execution owner to bind a coordinator credential to a campaign."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        allow_anonymous_dev: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Ting authorization base URL is required")
        if timeout_seconds <= 0:
            raise ValueError("Authorization timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._allow_anonymous_dev = allow_anonymous_dev
        self._client = client
        self._owns_client = client is None

    async def authorize(
        self,
        principal: Principal,
        campaign_id: str | None,
        operation: str,
        *,
        repository: str,
        base_sha: str | None = None,
        candidate_sha: str | None = None,
        candidate_tree: str | None = None,
        target_branch: str | None = None,
        policy_id: str | None = None,
        credential: str | None = None,
    ) -> None:
        if not principal.user_id or not principal.tenant_id:
            raise PermissionError("Delivery requires an authenticated user and tenant")
        if campaign_id is None:
            if operation != "resolve_ref":
                raise PermissionError("Campaign identity is required for delivery operations")
            return
        if not credential and not self._allow_anonymous_dev:
            raise PermissionError("Campaign delivery requires a workload credential")

        headers: dict[str, str] = {}
        if credential:
            headers["Authorization"] = credential
        else:
            headers.update(
                {
                    "X-Auth-User-Id": principal.user_id,
                    "X-Auth-Tenant": principal.tenant_id,
                    "X-Auth-Roles": ",".join(principal.roles),
                }
            )
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                f"{self._base_url}/api/v1/ting/delivery-executions/"
                f"{quote(campaign_id, safe='')}/delivery-authorizations",
                json={
                    "operation": operation,
                    "repository": repository,
                    **({"base_sha": base_sha} if base_sha else {}),
                    **({"candidate_sha": candidate_sha} if candidate_sha else {}),
                    **({"candidate_tree": candidate_tree} if candidate_tree else {}),
                    **({"target_branch": target_branch} if target_branch else {}),
                    **({"policy_id": policy_id} if policy_id else {}),
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Ting delivery authorization is unavailable") from exc
        finally:
            if self._owns_client:
                await client.aclose()
        if response.status_code in {401, 403, 404, 409, 422}:
            raise PermissionError("Ting denied the campaign delivery operation")
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"Ting delivery authorization failed with HTTP {response.status_code}"
            )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
