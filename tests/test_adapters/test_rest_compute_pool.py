"""Admin settings use the same role gate as storage, with no provider-specific routes."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.compute_fakes import pool_setup
from tests.test_adapters.test_rest_admin_settings import _FakeRepository, _mock_admin_principal
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.domain.compute import ComputeLeaseBusyError
from volundr.domain.models import Principal


@pytest.fixture
def setup():
    return pool_setup()


@pytest.fixture
def app(setup):
    app = FastAPI()
    app.state.admin_settings = {}
    app.include_router(
        create_admin_settings_router(
            _FakeRepository(), home_volumes_supported=False, compute_pool=setup[0]
        )
    )
    app.dependency_overrides[extract_principal] = _mock_admin_principal
    return app


def test_pool_controls_discovered_in_existing_settings_and_persisted(app, setup):
    with TestClient(app) as client:
        response = client.get("/api/v1/forge/settings")
        assert response.status_code == 200
        sections = {s["id"]: s for s in response.json()["sections"]}
        assert sections["compute"]["path"] == "/admin/settings/compute"
        assert sections["compute-status"]["fields"][0]["value"] == "No machines"
        profile = next(f for f in sections["compute"]["fields"] if f["key"] == "profile")
        assert profile["type"] == "select"
        assert profile["options"] == [{"label": "small", "value": "small"}]
        definition = sections["compute-profiles"]["fields"][0]
        assert definition["readOnly"]
        assert definition["value"] == "Size: Small"
        fields = {f["key"]: f["value"] for f in sections["compute"]["fields"]}
        fields.update(warm_min=2, max_machines=3, paused=True)
        assert client.patch("/api/v1/forge/admin/settings/compute", json=fields).status_code == 200
        current = client.get("/api/v1/forge/admin/settings/compute").json()
        assert current["policy"]["warm_min"] == 2
        assert current["policy"]["paused"]
        fields["warm_min"] = 4
        assert client.patch("/api/v1/forge/admin/settings/compute", json=fields).status_code == 422
        assert setup[2].policies["pool"].warm_min == 2


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "settings"),
        ("get", "admin/settings/compute"),
        ("patch", "admin/settings/compute"),
        ("patch", "admin/settings/compute/dispose"),
    ],
)
def test_non_admin_cannot_read_or_modify_compute(app, method, path):
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="user", tenant_id="tenant", email="user@test.com", roles=[]
    )
    with TestClient(app) as client:
        kwargs = {} if method == "get" else {"json": {"allocation_id": str(uuid4())}}
        assert client.request(method, "/api/v1/forge/" + path, **kwargs).status_code == 403


@pytest.mark.parametrize(
    "error,code",
    [
        (LookupError("missing"), 404),
        (ValueError("Stop the session"), 409),
        (ComputeLeaseBusyError("busy"), 409),
        (None, 200),
    ],
)
def test_disposal_reports_conflict_without_bypassing_preservation(app, setup, error, code):
    setup[0].dispose = AsyncMock(side_effect=error)
    with TestClient(app) as client:
        result = client.patch(
            "/api/v1/forge/admin/settings/compute/dispose", json={"allocation_id": str(uuid4())}
        )
        assert result.status_code == code


def test_unknown_profile_rejected_without_changing_pool(app, setup):
    with TestClient(app) as client:
        current = client.get("/api/v1/forge/admin/settings/compute").json()["policy"]
        result = client.patch(
            "/api/v1/forge/admin/settings/compute", json={**current, "profile": "missing"}
        )
        assert result.status_code == 422
        assert "configured" in result.json()["detail"]
        assert setup[2].policies["pool"].profile == current["profile"]


def test_reuse_policy_is_editable_in_admin_settings(app, setup):
    setup[4].supports_reuse = True
    with TestClient(app) as client:
        schema = client.get("/api/v1/forge/settings").json()
        section = next(s for s in schema["sections"] if s["id"] == "compute")
        field = next(f for f in section["fields"] if f["key"] == "reuse_policy")
        assert field["type"] == "select"
        assert {o["value"] for o in field["options"]} == {"reuse", "replace"}
        fields = {f["key"]: f["value"] for f in section["fields"]}
        fields["reuse_policy"] = "reuse"
        response = client.patch("/api/v1/forge/admin/settings/compute", json=fields)
        assert response.status_code == 200
        assert (
            client.get("/api/v1/forge/admin/settings/compute").json()["policy"]["reuse_policy"]
            == "reuse"
        )
