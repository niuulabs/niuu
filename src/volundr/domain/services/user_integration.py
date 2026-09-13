"""User integration service — ephemeral provider factory.

Resolves a user's integration connections into live provider instances
on the fly.  Provider instances are created per-request, used, then
discarded — credentials are never cached and never linger in memory.

In a multi-tenant system this ensures user A's tokens can never leak
to user B, even via a stale cache.
"""

from __future__ import annotations

import asyncio
import logging
from hashlib import sha256
from typing import Any

from volundr.domain.models import IntegrationConnection, IntegrationType
from volundr.domain.ports import (
    CredentialStorePort,
    GitProvider,
    IntegrationRepository,
    IssueTrackerProvider,
)
from volundr.utils import import_class

from .integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)


def git_token_path(connection_id: str) -> str:
    """Stable, session-mounted token location without credential values in pod specs."""
    return f"/run/secrets/git/{sha256(connection_id.encode()).hexdigest()}/token"


class UserIntegrationService:
    """Ephemeral factory for per-user provider instances.

    Combines shared/org-level providers (long-lived, from config) with
    per-user providers (ephemeral, from integration connections + credential
    store).  Every call fetches credentials fresh from the backend — nothing
    is cached.

    Parameters
    ----------
    shared_git_providers:
        Org-level git providers registered at startup.
    integration_repo:
        Persistence for ``IntegrationConnection`` objects.
    integration_registry:
        Catalog of ``IntegrationDefinition`` objects (read-only).
    credential_store:
        Backend for resolving secret values (Infisical, Vault, file, …).
    """

    def __init__(
        self,
        *,
        shared_git_providers: list[GitProvider] | None = None,
        shared_issue_providers: list[IssueTrackerProvider] | None = None,
        integration_repo: IntegrationRepository,
        integration_registry: IntegrationRegistry,
        credential_store: CredentialStorePort,
    ) -> None:
        self._shared_git = list(shared_git_providers or [])
        self._shared_issues = list(shared_issue_providers or [])
        self._integration_repo = integration_repo
        self._registry = integration_registry
        self._credential_store = credential_store

    def add_shared_issue_provider(self, provider: IssueTrackerProvider) -> None:
        """Register a shared/org-level issue tracker provider.

        Useful when the provider is created after this service (e.g.
        linear adapter wired later in app startup).
        """
        self._shared_issues.append(provider)

    # ------------------------------------------------------------------
    # Public API — typed convenience methods
    # ------------------------------------------------------------------

    async def get_git_providers(self, user_id: str) -> list[GitProvider]:
        """Return shared + user's source_control providers (ephemeral)."""
        user_providers = await self._instantiate_providers(
            user_id,
            IntegrationType.SOURCE_CONTROL,
        )
        return [*user_providers, *self._shared_git]

    async def get_issue_providers(
        self,
        user_id: str,
    ) -> list[IssueTrackerProvider]:
        """Return shared + user's issue_tracker providers (ephemeral)."""
        user_providers = await self._instantiate_providers(
            user_id,
            IntegrationType.ISSUE_TRACKER,
        )
        return [*self._shared_issues, *user_providers]

    async def get_providers(
        self,
        user_id: str,
        integration_type: IntegrationType,
    ) -> list[Any]:
        """Return shared + user's providers for any integration type."""
        shared = self._get_shared_for_type(integration_type)
        user_providers = await self._instantiate_providers(
            user_id,
            integration_type,
        )
        return [*shared, *user_providers]

    async def find_git_provider_for(
        self,
        repo_url: str,
        user_id: str,
    ) -> GitProvider | None:
        """Return the first git provider that supports *repo_url*.

        Resolves shared + user providers, returns the first match.
        Centralises the "iterate providers, check supports()" pattern
        so callers don't duplicate it.
        """
        providers = await self.get_git_providers(user_id)
        for provider in providers:
            if provider.supports(repo_url):
                return provider
        return None

    async def find_session_git_provider(
        self,
        repo_url: str,
        user_id: str,
        connections: tuple[IntegrationConnection, ...],
    ) -> tuple[IntegrationConnection, GitProvider] | None:
        """Resolve only source-control integrations attached to this session.

        A configured connection must be usable; never substitute a cluster token.
        """
        for conn in connections:
            if conn.integration_type != IntegrationType.SOURCE_CONTROL:
                continue
            if conn.owner_id != user_id or not conn.enabled:
                raise ValueError("Git integration is unavailable for the session owner")
            credentials = await self.get_credential_for_connection(user_id, conn)
            provider = self._build_provider(conn, credentials, self._schema_defaults(conn))
            if provider is None:
                raise ValueError(f"Cannot configure Git integration {conn.slug!r}")
            if not provider.supports(repo_url):
                continue
            if not credentials.get("token"):
                raise ValueError(f"Git integration {conn.slug!r} requires a token in Settings")
            if "token" in conn.config:
                raise ValueError(
                    "Store the Git token in the integration credential, not its config"
                )
            return conn, provider
        return None

    async def resolve_credentials(
        self,
        user_id: str,
        credential_name: str,
    ) -> dict[str, str]:
        """Fetch credential values by name for a user. Never cached."""
        if not credential_name:
            return {}

        cred_data = await self._credential_store.get_value(
            "user",
            user_id,
            credential_name,
        )
        return cred_data or {}

    async def get_credential_for_connection(
        self,
        user_id: str,
        connection: IntegrationConnection,
    ) -> dict[str, str]:
        """Fetch credential values for a connection. Never cached."""
        if not connection.credential_name:
            return {}

        cred_data = await self._credential_store.get_value(
            "user",
            user_id,
            connection.credential_name,
        )
        return cred_data or {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _instantiate_providers(
        self,
        user_id: str,
        integration_type: IntegrationType,
    ) -> list[Any]:
        """Create ephemeral provider instances from user's connections."""
        connections = await self._integration_repo.list_connections(
            user_id,
            integration_type=integration_type,
        )

        eligible = [c for c in connections if c.enabled and c.adapter]
        if not eligible:
            return []

        # Fetch all credentials concurrently — each is an independent I/O call
        cred_results = await asyncio.gather(
            *(self.get_credential_for_connection(user_id, c) for c in eligible),
        )

        providers: list[Any] = []
        for conn, cred_data in zip(eligible, cred_results):
            provider = self._build_provider(conn, cred_data, self._schema_defaults(conn))
            if provider is not None:
                providers.append(provider)

        return providers

    def _schema_defaults(self, conn: IntegrationConnection) -> dict[str, Any]:
        """Config values the catalog declares as defaults, e.g. GitHub's API URL.

        A connection saved from the wizard carries only what the person typed;
        the definition's defaults complete it the same way the form would.
        """
        definition = self._registry.get_definition(conn.slug)
        if definition is None:
            return {}
        properties = definition.config_schema.get("properties") or {}
        return {
            key: spec["default"]
            for key, spec in properties.items()
            if isinstance(spec, dict) and "default" in spec
        }

    @staticmethod
    def _build_provider(
        conn: IntegrationConnection,
        cred_data: dict[str, str],
        defaults: dict[str, Any] | None = None,
    ) -> Any | None:
        """Instantiate a provider from a connection + resolved credentials.

        Follows the dynamic adapter pattern: credential values are merged
        with connection config and passed as ``**kwargs`` to the adapter
        constructor.  No adapter-specific key mapping — adapters accept
        the kwargs they need and ignore the rest via ``**_extra``.
        """
        try:
            cls = import_class(conn.adapter)
        except (ImportError, AttributeError):
            logger.warning(
                "Cannot import adapter %s for connection %s",
                conn.adapter,
                conn.id,
            )
            return None

        # Merge credential data with connection config.
        # Config keys take precedence (explicit user settings override
        # credential-level defaults like "url").
        # Two accounts of one provider must stay apart in the repository list,
        # so an unnamed connection is known by its credential name.
        kwargs: dict[str, Any] = {
            "name": conn.config.get("name") or conn.credential_name or conn.slug,
            **(defaults or {}),
            **cred_data,
            **{key: value for key, value in conn.config.items() if value not in ("", None)},
        }

        try:
            return cls(**kwargs)
        except Exception:
            logger.exception(
                "Failed to instantiate %s for connection %s",
                conn.adapter,
                conn.id,
            )
            return None

    def _get_shared_for_type(self, integration_type: IntegrationType) -> list[Any]:
        """Return shared providers matching the given type."""
        match integration_type:
            case IntegrationType.SOURCE_CONTROL:
                return self._shared_git
            case IntegrationType.ISSUE_TRACKER:
                return self._shared_issues
            case IntegrationType.MESSAGING | IntegrationType.AI_PROVIDER:
                return []
        return []
