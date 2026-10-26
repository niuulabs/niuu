"""Record and revoke a resident's workload delegation on the platform, as its owner.

The Guild deploys residents onto clusters; the exchange host (niuu-shared) keeps
the delegation records that let each resident's own ServiceAccount act for its
owner. This adapter calls that host's ``/api/v1/tokens/workload/delegations`` with
the deploying person's own bearer, so the record is created under the API's own
rules (an owner delegates to themselves; a delegated caller may not) and the Guild
gains no authority of its own.

A resident that runs without platform access has no ServiceAccount of its own, and
an instance without ``config.workload_verifier`` is not delegation-enabled: both
are skipped, not failed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from niuu.domain.models import RegisteredInstance

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


_PATH = "/api/v1/tokens/workload/delegations"
#: Instance config key naming the exchange verifier of the instance's cluster.
VERIFIER_CONFIG_KEY = "workload_verifier"


def resident_workload_subject(resident: dict[str, Any]) -> str:
    """The ServiceAccount subject of a resident deployed as its own HelmRelease, or ''."""
    ref = resident.get("backend_ref") or resident.get("backendRef") or {}
    if not isinstance(ref, dict) or str(ref.get("kind") or "") != "HelmRelease":
        return ""
    namespace, name = str(ref.get("namespace") or ""), str(ref.get("name") or "")
    if not namespace or not name:
        return ""
    return f"system:serviceaccount:{namespace}:{name}"


class HttpResidentDelegationRegistrar:
    def __init__(
        self,
        platform_url: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = platform_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _client(self, authorization: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": authorization},
        )

    async def record(
        self, *, authorization: str, instance: RegisteredInstance, resident: dict[str, Any]
    ) -> dict[str, Any]:
        verifier = str((instance.config or {}).get(VERIFIER_CONFIG_KEY) or "")
        subject = resident_workload_subject(resident)
        if not verifier or not subject:
            return {"status": "skipped"}
        body = {
            "verifier": verifier,
            "subject": subject,
            "name": str(resident.get("resident_name") or resident.get("name") or ""),
            "residentId": str(resident.get("id") or ""),
        }
        try:
            async with self._client(authorization) as client:
                response = await client.post(_PATH, json=body)
        except httpx.HTTPError as exc:
            logger.error("Recording delegation for %s failed: %s", subject, exc)
            return {"status": "failed", "detail": str(exc)}
        if response.status_code >= 400:
            logger.error(
                "Recording delegation for %s failed: %s %s",
                subject,
                response.status_code,
                response.text[:300],
            )
            return {"status": "failed", "detail": f"{response.status_code}: {response.text[:300]}"}
        return {"status": "recorded", "id": str(response.json().get("id") or "")}

    async def revoke(
        self, *, authorization: str, instance: RegisteredInstance, resident_id: str
    ) -> dict[str, Any]:
        if not str((instance.config or {}).get(VERIFIER_CONFIG_KEY) or "") or not resident_id:
            return {"status": "skipped"}
        try:
            async with self._client(authorization) as client:
                listed = await client.get(_PATH, params={"residentId": resident_id})
                listed.raise_for_status()
                revoked = 0
                for record in listed.json():
                    deleted = await client.delete(f"{_PATH}/{record['id']}")
                    if deleted.status_code == 204:
                        revoked += 1
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.error(
                "Revoking delegations of resident %s failed: %s",
                _sanitize_log(resident_id),
                _sanitize_log(exc),
            )
            return {"status": "failed", "detail": str(exc)}
        return {"status": "revoked", "count": revoked}
