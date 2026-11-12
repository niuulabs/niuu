"""Looks a project memory mount up in the hosted Mimir's registry, over HTTP."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from niuu.domain.models import Principal
from niuu.ports.workload_identity import WorkloadTokenIssuer
from volundr.domain.project_memory import (
    MemoryDirectoryUnavailableError,
    MemoryMountDirectory,
    RegistryTarget,
)

logger = logging.getLogger(__name__)

_KNOB = "projects.memory.directory_kwargs"


class MimirRegistryHttpDirectory(MemoryMountDirectory):
    """Asks the hosted Mimir (``GET {base_url}/mimir/registry/mounts``) for its entries.

    The request carries a short-lived workload token Forge mints for the session's owner
    (audience ``mimir``, with Forge as the acting workload), so Mimir answers with the
    entries that owner's organization can reach. No personal access token is involved.
    Forge's ``workload_identity`` must be enabled and list ``mimir`` among its audiences.

    A Mimir that cannot be reached, that refuses the token, or that answers with anything
    but its registry raises ``MemoryDirectoryUnavailableError`` naming this setting: an
    entry is "not found" only when Mimir answered and it was not in the answer.
    """

    def __init__(
        self,
        base_url: str,
        token_issuer: WorkloadTokenIssuer,
        audience: str = "mimir",
        workload_name: str = "forge-project-memory",
        timeout_seconds: float = 10.0,
        **_kwargs: object,
    ) -> None:
        if not base_url.strip():
            raise ValueError(f"{_KNOB}.base_url is empty; set it to the hosted Mimir's origin")
        if not token_issuer.enabled:
            raise ValueError(
                "MimirRegistryHttpDirectory needs workload_identity.enabled: it asks Mimir "
                "with a workload token. Enable workload identity (with 'mimir' among its "
                "audiences), or use the file directory"
            )
        if timeout_seconds <= 0:
            raise ValueError(f"{_KNOB}.timeout_seconds must be positive")
        self._url = f"{base_url.rstrip('/')}/mimir/registry/mounts"
        self._issuer = token_issuer
        self._audience = audience
        self._workload = workload_name
        self._timeout = timeout_seconds

    async def resolve(
        self, entry_id: str, *, tenant_id: str, owner_id: str
    ) -> RegistryTarget | None:
        for item in await self._entries(tenant_id, owner_id):
            if isinstance(item, dict) and item.get("id") == entry_id:
                return self._target(item)
        return None

    async def _entries(self, tenant_id: str, owner_id: str) -> list[Any]:
        try:
            token = self._issuer.issue_token(
                principal=Principal(user_id=owner_id, email="", tenant_id=tenant_id, roles=[]),
                workload_subject=self._workload,
                workload_name=self._workload,
                audiences=[self._audience],
            ).token
        except Exception as exc:
            raise MemoryDirectoryUnavailableError(
                f"Forge could not mint a workload token for audience {self._audience!r} to ask "
                f"Mimir for its registry: {exc}. Add {self._audience!r} to "
                "workload_identity.audiences and enable workload identity."
            ) from exc
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise MemoryDirectoryUnavailableError(
                f"Mimir at {self._url} could not be reached ({exc!r}); check "
                f"{_KNOB}.base_url and that Mimir is up"
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise MemoryDirectoryUnavailableError(
                f"Mimir at {self._url} answered {response.status_code} to the registry "
                f"listing ({response.text[:200]!r}); check that it accepts Forge's workload "
                f"token (audience {self._audience!r}) and {_KNOB}.base_url"
            )
        try:
            entries = response.json()
        except ValueError as exc:
            raise MemoryDirectoryUnavailableError(
                f"Mimir at {self._url} did not answer with JSON; check {_KNOB}.base_url"
            ) from exc
        if not isinstance(entries, list):
            raise MemoryDirectoryUnavailableError(
                f"Mimir at {self._url} did not answer with a list of registry entries; "
                f"check {_KNOB}.base_url"
            )
        return entries

    @staticmethod
    def _target(item: dict[str, Any]) -> RegistryTarget:
        # An entry's path is a path on Mimir's own disk: a session cannot mount it, so
        # only the url or adapter of an entry is passed on. An entry with neither is
        # refused by the resolver, naming the mount.
        return RegistryTarget(
            id=str(item["id"]),
            name=str(item.get("name") or item["id"]),
            enabled=item.get("enabled", True) is not False,
            role=str(item.get("role") or "shared"),
            url=str(item.get("url") or ""),
            adapter=str(item.get("adapter") or ""),
            kwargs=dict(item.get("kwargs") or {}),
            secret_kwargs_env=dict(item.get("secret_kwargs_env") or {}),
            auth_ref=item.get("auth_ref") or None,
            categories=tuple(item.get("categories") or ()),
            default_read_priority=int(item.get("default_read_priority", 10)),
            facts=item.get("facts") is True,
        )
