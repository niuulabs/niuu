"""Registering the install's OAuth applications from the wizard."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_integrations import create_integrations_router
from volundr.adapters.outbound.file_credential_store import FileCredentialStore
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.adapters.outbound.oauth_device_runner import OAuthDeviceFlowRunner
from volundr.config import _default_integration_definitions
from volundr.domain.models import Principal
from volundr.domain.services.credential_enrollment import CredentialEnrollmentService
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.oauth_clients import OAuthClient, OAuthClientRegistry
from volundr.domain.services.tracker_factory import TrackerFactory


class _Enrollments:
    """Never consulted here: the catalog's availability flag is answered by the runner."""


def _client(tmp_path, *, with_registry: bool = True) -> TestClient:
    store = FileCredentialStore(base_dir=str(tmp_path))
    integrations = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )
    clients = OAuthClientRegistry(
        credential_store=store,
        integration_registry=integrations,
        configured={"gitlab": OAuthClient(slug="gitlab", client_id="glcfg", source="configured")},
    )
    repo = InMemoryIntegrationRepository()
    enrollment = CredentialEnrollmentService(
        repository=_Enrollments(),  # type: ignore[arg-type]
        runner=OAuthDeviceFlowRunner(registry=integrations, clients=clients),
        integration_repository=repo,
        integration_registry=integrations,
        credential_store=store,
    )
    app = FastAPI()
    app.include_router(
        create_integrations_router(
            repo,
            TrackerFactory(store),
            registry=integrations,
            credential_store=store,
            credential_enrollment_service=enrollment,
            oauth_clients=clients if with_registry else None,
        )
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="owner-1", tenant_id="t", email="o@example.test", roles=["volundr:developer"]
    )
    return TestClient(app)


def _catalog_entry(client: TestClient, slug: str) -> dict:
    rows = client.get("/api/v1/integrations/catalog").json()
    return next(row for row in rows if row["slug"] == slug)


def test_registering_an_application_turns_sign_in_on(tmp_path) -> None:
    client = _client(tmp_path)

    before = _catalog_entry(client, "github")
    assert before["sign_in_available"] is False
    assert before["sign_in_needs_app"] is True
    assert _catalog_entry(client, "gitlab")["sign_in_available"] is True
    assert _catalog_entry(client, "anthropic")["sign_in_needs_app"] is False

    response = client.put(
        "/api/v1/integrations/oauth-clients/github",
        json={"client_id": "Iv1.mine", "client_secret": "shh"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "slug": "github",
        "client_id": "Iv1.mine",
        "has_secret": True,
        "source": "registered",
    }

    after = _catalog_entry(client, "github")
    assert after["sign_in_available"] is True
    assert after["sign_in_needs_app"] is False
    listed = client.get("/api/v1/integrations/oauth-clients").json()
    assert listed == [
        {"slug": "github", "client_id": "Iv1.mine", "has_secret": True, "source": "registered"},
        {"slug": "gitlab", "client_id": "glcfg", "has_secret": False, "source": "configured"},
    ]


def test_registration_is_refused_for_non_oauth_integrations_and_empty_ids(tmp_path) -> None:
    client = _client(tmp_path)
    assert (
        client.put("/api/v1/integrations/oauth-clients/anthropic", json={"client_id": "x"})
    ).status_code == 422
    assert (
        client.put("/api/v1/integrations/oauth-clients/github", json={"client_id": "  "})
    ).status_code == 422


def test_removal_forgets_registered_applications_only(tmp_path) -> None:
    client = _client(tmp_path)
    client.put("/api/v1/integrations/oauth-clients/github", json={"client_id": "Iv1.mine"})
    assert client.delete("/api/v1/integrations/oauth-clients/github").status_code == 204
    assert _catalog_entry(client, "github")["sign_in_needs_app"] is True
    assert client.delete("/api/v1/integrations/oauth-clients/gitlab").status_code == 404


def test_without_a_registry_the_routes_say_so(tmp_path) -> None:
    client = _client(tmp_path, with_registry=False)
    assert client.get("/api/v1/integrations/oauth-clients").status_code == 503
    assert _catalog_entry(client, "github")["sign_in_needs_app"] is False
