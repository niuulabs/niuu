"""The OAuth applications this install signs in through.

Every install uses applications its people own: a GitHub OAuth App or a
GitLab application with the device grant enabled. A provider can have
several, one per account when the accounts live in different organisations
or need different apps; each account remembers which application it signed
in through. Client ids come from configuration (``oauth.clients``, the
provider's ``default`` app) or are registered from the setup wizard and kept
in the encrypted credential store.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from volundr.domain.models import SecretType
from volundr.domain.ports import CredentialStorePort
from volundr.domain.services.integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)

OAUTH_CLIENTS_OWNER_TYPE = "system"
OAUTH_CLIENTS_OWNER_ID = "oauth-clients"
SOURCE_CONFIGURED = "configured"
SOURCE_REGISTERED = "registered"
DEFAULT_APP = "default"
# Stored as ``<slug>`` for the default app and ``<slug>--<app>`` otherwise.
_APP_SEPARATOR = "--"
_APP_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass(frozen=True)
class OAuthClient:
    slug: str
    client_id: str
    client_secret: str = ""
    source: str = SOURCE_REGISTERED
    app: str = DEFAULT_APP


class OAuthClientError(ValueError):
    """A registration the install cannot use."""


def app_key(name: str) -> str:
    """The key an application is stored under: lower-case, dashes, ``default`` when empty."""
    key = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return key or DEFAULT_APP


def _storage_name(slug: str, app: str) -> str:
    return slug if app == DEFAULT_APP else f"{slug}{_APP_SEPARATOR}{app}"


def _parse_storage_name(name: str) -> tuple[str, str]:
    slug, separator, app = name.partition(_APP_SEPARATOR)
    return (slug, app) if separator else (name, DEFAULT_APP)


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
        self._configured: dict[tuple[str, str], OAuthClient] = {
            (slug, DEFAULT_APP): OAuthClient(
                slug=slug,
                client_id=client.client_id,
                client_secret=client.client_secret,
                source=SOURCE_CONFIGURED,
                app=DEFAULT_APP,
            )
            for slug, client in (configured or {}).items()
            if client.client_id
        }
        self._registered: dict[tuple[str, str], OAuthClient] = {}

    async def load(self) -> None:
        """Read the registered clients from the store; cheap enough to repeat."""
        registered: dict[tuple[str, str], OAuthClient] = {}
        for stored in await self._store.list(OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID):
            values = await self._store.get_value(
                OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, stored.name
            )
            if not values or not values.get("client_id"):
                continue
            slug, app = _parse_storage_name(stored.name)
            registered[(slug, app)] = OAuthClient(
                slug=slug,
                client_id=values["client_id"],
                client_secret=values.get("client_secret", ""),
                app=app,
            )
        self._registered = registered

    def get(self, slug: str, app: str = DEFAULT_APP) -> OAuthClient | None:
        """Registered wins over configured, so the wizard can replace a stale id."""
        key = (slug, app or DEFAULT_APP)
        return self._registered.get(key) or self._configured.get(key)

    def has_any(self, slug: str) -> bool:
        return any(client.slug == slug for client in self.list())

    def list(self) -> list[OAuthClient]:
        merged = dict(self._configured)
        merged.update(self._registered)
        return [merged[key] for key in sorted(merged)]

    def list_for(self, slug: str) -> list[OAuthClient]:
        return [client for client in self.list() if client.slug == slug]

    def supports(self, slug: str) -> bool:
        """Whether this integration signs in through an OAuth application at all."""
        definition = self._registry.get_definition(slug)
        return (
            definition is not None
            and definition.oauth is not None
            and bool(definition.oauth.device_authorization_url)
        )

    async def register(
        self, slug: str, client_id: str, client_secret: str = "", app: str = DEFAULT_APP
    ) -> OAuthClient:
        if not self.supports(slug):
            raise OAuthClientError(f"{slug!r} does not sign in through an OAuth application")
        client_id = client_id.strip()
        if not client_id:
            raise OAuthClientError("A client id is required")
        app = app or DEFAULT_APP
        if not _APP_KEY.match(app):
            raise OAuthClientError(
                "An application name is letters, digits and dashes, at most 40 characters"
            )
        client = OAuthClient(
            slug=slug, client_id=client_id, client_secret=client_secret.strip(), app=app
        )
        await self._store.store(
            OAUTH_CLIENTS_OWNER_TYPE,
            OAUTH_CLIENTS_OWNER_ID,
            _storage_name(slug, app),
            SecretType.GENERIC,
            {"client_id": client.client_id, "client_secret": client.client_secret},
            {"integration": slug, "app": app, "source": SOURCE_REGISTERED},
        )
        self._registered[(slug, app)] = client
        logger.info("OAuth application %r registered for %s", app, slug)
        return client

    async def remove(self, slug: str, app: str = DEFAULT_APP) -> None:
        app = app or DEFAULT_APP
        if (slug, app) not in self._registered:
            raise OAuthClientError(f"No registered OAuth application {app!r} for {slug!r}")
        await self._store.delete(
            OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, _storage_name(slug, app)
        )
        self._registered.pop((slug, app), None)
