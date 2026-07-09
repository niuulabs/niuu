"""Tests for the internal OpenShell credential token endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_openshell_credentials import (
    create_openshell_credentials_router,
)
from volundr.domain.ports import OpenShellCredentialGrantToken


class Broker:
    def __init__(self) -> None:
        self.request = None

    async def exchange_credential_grant(self, **kwargs):
        self.request = kwargs
        return OpenShellCredentialGrantToken(access_token="secret-from-openbao")


def test_token_endpoint_accepts_rfc7523_client_assertion_form() -> None:
    broker = Broker()
    app = FastAPI()
    app.include_router(create_openshell_credentials_router(broker))

    response = TestClient(app).post(
        "/api/v1/internal/openshell/credential-token",
        data={
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-spiffe",
            "client_assertion": "signed-svid",
            "audience": "niuu:credential:volundr-session-grant",
            "scope": "",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "secret-from-openbao",
        "token_type": "Bearer",
        "expires_in": 300,
        "scope": "",
    }
    assert broker.request["client_assertion"] == "signed-svid"


def test_token_endpoint_returns_oauth_error_without_secret_details() -> None:
    class RejectingBroker:
        async def exchange_credential_grant(self, **_kwargs):
            raise ValueError("credential provider is not attached to this sandbox")

    app = FastAPI()
    app.include_router(create_openshell_credentials_router(RejectingBroker()))

    response = TestClient(app).post(
        "/api/v1/internal/openshell/credential-token",
        content=b"grant_type=client_credentials",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_grant"
    assert response.headers["cache-control"] == "no-store"
