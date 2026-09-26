"""Factory for resolving per-owner VolundrHTTPAdapter instances."""

from __future__ import annotations

import logging
from typing import Any

from niuu.domain.models import Principal, RegisteredInstance
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.http_auth import HttpAuthPort
from ting.adapters.guild_instances import GuildInstanceRegistryClient
from ting.adapters.volundr_http import VolundrHTTPAdapter
from ting.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)


class GuildRegistryUnavailableError(RuntimeError):
    """Raised when Guild's shared instance registry cannot be reached.

    Previously this was swallowed into an empty adapter list, which was
    indistinguishable from "this user has no Volundr connections configured"
    — a real, common state. Dispatch and the activity subscriber need to tell
    those two apart, so this is raised instead (see
    ``.claude/rules/no-fallbacks.md``).
    """


_VALID_CREDENTIAL_SCOPES = {"user", "tenant"}


class CredentialBindingError(ValueError):
    """Raised when an instance's config.credentialBinding is unusable.

    A malformed binding must not be silently treated as "no credential" —
    that would launch the target unauthenticated instead of telling the
    operator their binding is broken (see .claude/rules/no-fallbacks.md).
    """


def _credential_binding(config: dict[str, Any]) -> tuple[str, str]:
    """Resolve the (name, scope) of the credential bound to an instance.

    Canonical shape is ``config.credentialBinding = {"name": ..., "scope":
    ...}`` — what the Guild registration UI writes. The legacy flat
    ``config.credential_name`` (always user-scoped) is honoured only when
    ``credentialBinding`` is entirely absent — once a binding is present it
    is the single source of truth, malformed or not.
    """
    binding = config.get("credentialBinding")
    if binding is not None:
        if not isinstance(binding, dict):
            raise CredentialBindingError(
                f"config.credentialBinding must be an object, got {type(binding).__name__}"
            )
        name = str(binding.get("name") or "").strip()
        if not name:
            raise CredentialBindingError(
                "config.credentialBinding is present but has no name; set "
                "credentialBinding.name, or remove credentialBinding to leave the "
                "instance unauthenticated."
            )
        scope = str(binding.get("scope") or "").strip() or "user"
        if scope not in _VALID_CREDENTIAL_SCOPES:
            raise CredentialBindingError(
                f"config.credentialBinding.scope {scope!r} must be one of "
                f"{sorted(_VALID_CREDENTIAL_SCOPES)}"
            )
        return name, scope
    legacy_name = str(config.get("credential_name") or "").strip()
    return legacy_name, "user"


class LocalVolundrAdapterFactory:
    """Volundr adapter factory for mini/local mode.

    Always returns a single VolundrHTTPAdapter pointing at the local
    server with no PAT required.  Same interface as VolundrAdapterFactory
    so all callers (dispatch, activity subscriber, review engine) work
    without fallback logic.
    """

    def __init__(self, url: str) -> None:
        self._adapter = VolundrHTTPAdapter(base_url=url, name="local", config={})

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return [self._adapter]

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[VolundrPort], int]:
        """Local mode never silently skips an instance — nothing is ever unresolved."""
        return [self._adapter], 0

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._adapter

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return [self._adapter]

    async def primary_for_principal(self, principal: Principal) -> VolundrPort | None:
        return self._adapter

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        return self._adapter


class VolundrAdapterFactory:
    """Resolve VolundrHTTPAdapter instances from Guild's shared registry."""

    def __init__(
        self,
        registry: GuildInstanceRegistryClient,
        credential_store: CredentialStorePort,
        *,
        allow_unauthenticated: bool = False,
        target_auth: HttpAuthPort | None = None,
    ) -> None:
        self._registry = registry
        self._credential_store = credential_store
        self._allow_unauthenticated = allow_unauthenticated
        self._target_auth = target_auth

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        """Return all authenticated VolundrHTTPAdapter instances for *owner_id*.

        Returns an empty list when the user has no enabled CODE_FORGE
        connections with valid credentials.
        """
        adapters, _unresolved = await self._resolve_connections(owner_id)
        return adapters

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[VolundrPort], int]:
        """Like ``for_owner``, but also reports how many registered instances
        could not be resolved into a usable adapter this call (missing
        credential, or adapter construction failed — see
        ``_resolve_connections``).

        A caller that needs to know whether it saw *every* registered
        cluster — not just every cluster that happened to resolve cleanly —
        must use this instead of ``for_owner``. Treating a silently-skipped
        instance the same as "this owner has no such cluster" is exactly
        the silent-degradation pattern ``.claude/rules/no-fallbacks.md``
        forbids (see the activity subscriber's run-reconciliation, which
        must not fail a run just because one of the owner's clusters
        couldn't be resolved this cycle).
        """
        return await self._resolve_connections(owner_id)

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        """Return the first (primary) authenticated adapter, or ``None``."""
        adapters, _unresolved = await self._resolve_connections(owner_id)
        if adapters:
            return adapters[0]
        return None

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        adapters, _unresolved = await self._resolve_connections(
            principal.user_id, principal=principal
        )
        return adapters

    async def primary_for_principal(self, principal: Principal) -> VolundrPort | None:
        adapters, _unresolved = await self._resolve_connections(
            principal.user_id, principal=principal
        )
        if adapters:
            return adapters[0]
        return None

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        """Resolve the owner's adapter for a specific connection.

        Matches by target id or name — the same identifiers the launch path
        accepts — so a campaign resolves to the Volundr instance its session
        actually lives on. Returns None when the connection is gone.
        """
        wanted = str(connection_id or "").strip()
        if not wanted:
            return None
        adapters, _unresolved = await self._resolve_connections(owner_id)
        for adapter in adapters:
            if wanted in {adapter.target_id, adapter.name}:
                return adapter
        return None

    async def _resolve_connections(
        self,
        owner_id: str,
        *,
        principal: Principal | None = None,
    ) -> tuple[list[VolundrPort], int]:
        """Resolve Guild-registered Volundr adapters.

        Returns ``(adapters, unresolved_count)`` — *unresolved_count* is how
        many of the owner's registered instances were skipped this call
        (missing credential, or adapter construction failed). Skips are
        logged loudly (ERROR, not WARNING — see ``.claude/rules/no-fallbacks.md``)
        and counted rather than silently vanishing, so a caller that needs
        to know it saw every registered cluster (``for_owner_with_unresolved``)
        can tell "empty because zero clusters are registered" apart from
        "empty because every registered cluster failed to resolve".
        """
        if principal is None:
            principal = Principal(
                user_id=owner_id,
                email="",
                tenant_id="default",
                roles=["volundr:developer"],
            )

        adapters: list[VolundrPort] = []
        unresolved = 0
        try:
            instances = await self._registry.list_volundr_targets(principal)
        except Exception as exc:
            raise GuildRegistryUnavailableError(
                f"Failed to load Volundr targets from Guild for owner {owner_id}: {exc}"
            ) from exc

        for instance in sorted(
            instances,
            key=lambda item: (0 if item.is_default else 1, item.name.lower(), item.created_at),
        ):
            # Credential resolution (Guild config parsing + the credential
            # store call) is deliberately NOT caught here: a malformed
            # binding or a credential-store outage is a fault, and catching
            # it here would silently drop the instance — indistinguishable
            # from "this user has no connections" (see
            # .claude/rules/no-fallbacks.md). Only adapter construction,
            # a local, side-effect-free step, is narrowly guarded below.
            credential_name, credential_scope = _credential_binding(instance.config)
            token = await self._resolve_instance_token(
                instance, owner_id, credential_name, credential_scope
            )
            if credential_name and not token and not self._allow_unauthenticated:
                unresolved += 1
                logger.error(
                    "Skipping Guild Volundr target %s (%s) without a usable credential "
                    "— its runs are treated as unknown, not missing, until this is fixed",
                    instance.id,
                    instance.name,
                )
                continue
            try:
                adapters.append(
                    VolundrHTTPAdapter(
                        base_url=instance.base_url,
                        api_key=token,
                        name=instance.name,
                        target_id=instance.id,
                        tags=instance.tags,
                        auth=self._target_auth,
                        config=instance.config,
                    )
                )
            except Exception:
                unresolved += 1
                logger.error(
                    "Failed to construct a Volundr adapter for instance %s (owner=%s)",
                    instance.id,
                    owner_id,
                    exc_info=True,
                )
        return adapters, unresolved

    async def _resolve_instance_token(
        self,
        instance: RegisteredInstance,
        owner_id: str,
        credential_name: str,
        credential_scope: str,
    ) -> str | None:
        if not credential_name:
            return None
        if credential_scope == "tenant":
            if not instance.tenant_id:
                raise CredentialBindingError(
                    f"Instance {instance.id} ({instance.name}) binds a tenant-scoped "
                    f"credential ({credential_name!r}) but has no tenant_id; set the "
                    "instance's tenant, or bind a user-scoped credential instead."
                )
            scope_owner = instance.tenant_id
        else:
            scope_owner = owner_id
        credential = await self._credential_store.get_value(
            credential_scope, scope_owner, credential_name
        )
        if credential is None:
            return None
        token = credential.get("token")
        return str(token).strip() if token else None
