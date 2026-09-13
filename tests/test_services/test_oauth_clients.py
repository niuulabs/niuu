"""The install's own OAuth applications: configured as the base, registered on top."""

from __future__ import annotations

import pytest

from volundr.adapters.outbound.file_credential_store import FileCredentialStore
from volundr.config import _default_integration_definitions
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.oauth_clients import (
    OAUTH_CLIENTS_OWNER_ID,
    OAUTH_CLIENTS_OWNER_TYPE,
    SOURCE_CONFIGURED,
    SOURCE_REGISTERED,
    OAuthClient,
    OAuthClientError,
    OAuthClientRegistry,
    app_key,
)


def _integrations() -> IntegrationRegistry:
    return IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )


def _registry(store: FileCredentialStore, **configured: OAuthClient) -> OAuthClientRegistry:
    return OAuthClientRegistry(
        credential_store=store, integration_registry=_integrations(), configured=configured
    )


@pytest.fixture
def store(tmp_path) -> FileCredentialStore:
    return FileCredentialStore(base_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_registered_applications_survive_a_restart_and_win_over_config(store) -> None:
    configured = OAuthClient(
        slug="github", client_id="Iv1.cfg", client_secret="", source=SOURCE_CONFIGURED
    )
    registry = _registry(store, github=configured)
    await registry.load()
    assert registry.get("github") == configured
    assert registry.get("gitlab") is None

    registered = await registry.register("gitlab", "  glpub  ", "shh")
    assert registered == OAuthClient(
        slug="gitlab", client_id="glpub", client_secret="shh", source=SOURCE_REGISTERED
    )
    await registry.register("github", "Iv1.mine")

    fresh = _registry(store, github=configured)
    await fresh.load()
    assert fresh.get("gitlab") == registered
    assert fresh.get("github") is not None
    assert fresh.get("github").client_id == "Iv1.mine"  # the wizard replaces a stale config id
    assert [client.slug for client in fresh.list()] == ["github", "gitlab"]

    stored = await store.get_value(OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, "gitlab")
    assert stored == {"client_id": "glpub", "client_secret": "shh"}


@pytest.mark.asyncio
async def test_a_provider_can_have_an_application_per_account(store) -> None:
    registry = _registry(store)
    await registry.load()
    await registry.register("github", "Iv1.personal")
    niuu = await registry.register("github", "Iv1.org", "", app="niuu-org")

    assert niuu.app == "niuu-org"
    assert registry.get("github").client_id == "Iv1.personal"
    assert registry.get("github", "niuu-org") == niuu
    assert registry.get("github", "nope") is None
    assert registry.has_any("github") and not registry.has_any("gitlab")
    assert [c.app for c in registry.list_for("github")] == ["default", "niuu-org"]
    stored = await store.get_value(
        OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, "github--niuu-org"
    )
    assert stored == {"client_id": "Iv1.org", "client_secret": ""}

    fresh = _registry(store)
    await fresh.load()
    assert fresh.get("github", "niuu-org") == niuu
    await fresh.remove("github", "niuu-org")
    assert fresh.get("github", "niuu-org") is None
    assert fresh.get("github").client_id == "Iv1.personal"

    with pytest.raises(OAuthClientError, match="letters, digits and dashes"):
        await registry.register("github", "x", app="Niuu Org")
    assert app_key(" Niuu Org! ") == "niuu-org"
    assert app_key("") == "default"


@pytest.mark.asyncio
async def test_only_device_flow_integrations_take_an_application(store) -> None:
    registry = _registry(store)
    await registry.load()
    assert registry.supports("github")
    assert registry.supports("gitlab")
    assert not registry.supports("anthropic")
    assert not registry.supports("nope")
    with pytest.raises(OAuthClientError, match="does not sign in through"):
        await registry.register("anthropic", "x")
    with pytest.raises(OAuthClientError, match="client id is required"):
        await registry.register("github", "   ")


@pytest.mark.asyncio
async def test_remove_forgets_registered_applications_only(store) -> None:
    configured = OAuthClient(slug="github", client_id="Iv1.cfg", source=SOURCE_CONFIGURED)
    registry = _registry(store, github=configured)
    await registry.load()
    await registry.register("gitlab", "glpub")
    await registry.remove("gitlab")
    assert registry.get("gitlab") is None
    assert await store.get(OAUTH_CLIENTS_OWNER_TYPE, OAUTH_CLIENTS_OWNER_ID, "gitlab") is None
    with pytest.raises(OAuthClientError, match="No registered"):
        await registry.remove("github")
    assert registry.get("github") == configured
