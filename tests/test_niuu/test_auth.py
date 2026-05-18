from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal


def test_extract_principal_defaults_blank_forwarded_tenant_to_default():
    app = FastAPI()

    @app.get("/test")
    async def endpoint(principal: Principal = Depends(extract_principal)):
        return {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
        }

    client = TestClient(app)
    response = client.get(
        "/test",
        headers={
            "x-auth-user-id": "shared-user",
            "x-auth-email": "shared@example.com",
            "x-auth-tenant": "",
            "x-auth-roles": "volundr:developer",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "shared-user",
        "tenant_id": "default",
    }
