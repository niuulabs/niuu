"""The OAuth applications this install signs in through.

Every install uses applications its operator or users own: a GitHub OAuth App
or a GitLab application with the device grant enabled. Their client ids come
from configuration (``oauth.clients``) or are registered from the setup
wizard and kept in the encrypted credential store, so nothing has to be
edited by hand and nothing depends on an application someone else owns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from volundr.domain.models import SecretType
from volundr.domain.ports import CredentialStorePort
from volundr.domain.services.integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)

OAUTH_CLIENTS_OWNER_TYPE = "system"
OAUTH_CLIENTS_OWNER_ID = "oauth-clients"
SOURCE_CONFIGURED = "configured"
SOURCE_REGISTERED = "registered"


@dataclass(frozen=True)
class OAuthClient:
    slug: str
    client_id: str
    client_secret: str = ""
    source: str = SOURCE_REGISTERED


class OAuthClientError(ValueError):
    """A registration the install cannot use."""


class OAuthClientRegistry:
    """Configured clients as the base, registered ones on top, all in memory after ``load``."""

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort,
        integration_registry: IntegrationRegistry,
        configured: dict[str, OAuthClient] | None = None,
    ) -> None:
        self._store = credential_store
        self._registry = integration_registry
        self._configured = {
            slug: client for slug, client in (configured or {}).items() if client.client_id
        }
        self._registered: dict[str, OAuthClient] = {}
        self._loaded = False

    async def load(self) -> None:
        """Read the registered clients from the store; cheap enough to repeat."""
        registered: dict[str, OAuthClient] = {}
        for stored in await self._store.list(OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID):
            values = await self._store.get_value(
                OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, stored.name
            )
            if not values or not values.get("client_id"):
                continue
            registered[stored.name] = OAuthClient(
                slug=stored.name,
                client_id=values["client_id"],
                client_secret=values.get("client_secret", ""),
            )
        self._registered = registered
        self._loaded = True

    def get(self, slug: str) -> OAuthClient | None:
        """Registered wins over configured, so the wizard can replace a stale id."""
        return self._registered.get(slug) or self._configured.get(slug)

    def list(self) -> list[OAuthClient]:
        merged = dict(self._configured)
        merged.update(self._registered)
        return [merged[slug] for slug in sorted(merged)]

    def supports(self, slug: str) -> bool:
        """Whether this integration signs in through an OAuth application at all."""
        definition = self._registry.get_definition(slug)
        return (
            definition is not None
            and definition.oauth is not None
            and bool(definition.oauth.device_authorization_url)
        )

    async def register(self, slug: str, client_id: str, client_secret: str = "") -> OAuthClient:
        if not self.supports(slug):
            raise OAuthClientError(f"{slug!r} does not sign in through an OAuth application")
        client_id = client_id.strip()
        if not client_id:
            raise OAuthClientError("A client id is required")
        client = OAuthClient(slug=slug, client_id=client_id, client_secret=client_secret.strip())
        await self._store.store(
            OAUTH_CLIENTS_OWNER_TYPE,
            OAUTH_CLIENTS_OWNER_ID,
            slug,
            SecretType.GENERIC,
            {"client_id": client.client_id, "client_secret": client.client_secret},
            {"integration": slug, "source": SOURCE_REGISTERED},
        )
        self._registered[slug] = client
        logger.info("OAuth client registered for %s", slug)
        return client

    async def remove(self, slug: str) -> None:
        if slug not in self._registered:
            raise OAuthClientError(f"No registered OAuth client for {slug!r}")
        await self._store.delete(OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, slug)
        self._registered.pop(slug, None)
