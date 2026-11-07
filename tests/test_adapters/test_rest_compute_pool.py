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


def _compute_fields(client) -> dict:
    schema = client.get("/api/v1/forge/settings").json()
    section = next(s for s in schema["sections"] if s["id"] == "compute")
    return {f["key"]: f for f in section["fields"]}


def test_slot_settings_are_labelled_and_unset_limits_post_back_as_null(app, setup):
    with TestClient(app) as client:
        fields = _compute_fields(client)

        assert fields["slots_per_machine"]["label"] == "Sessions per machine"
        assert fields["slots_per_machine"]["type"] == "number"
        assert fields["slots_per_machine"]["value"] == 1
        assert not fields["slots_per_machine"]["nullable"]
        assert fields["warm_min"]["label"] == "Free warm slots"
        # Unset optional limits are shown as empty numbers the form saves back as null.
        for key in ("max_sessions_per_machine", "max_machine_age_seconds"):
            assert fields[key]["type"] == "number"
            assert fields[key]["nullable"] is True
            assert fields[key]["value"] is None
            assert fields[key]["placeholder"] == "Unset"
        body = {key: field["value"] for key, field in fields.items()}
        assert client.patch("/api/v1/forge/admin/settings/compute", json=body).status_code == 200
        stored = setup[2].policies["pool"]
        assert stored.max_sessions_per_machine is None
        assert stored.max_machine_age_seconds is None


def test_set_drain_limits_are_labelled_numbers(app, setup):
    setup[4].supports_reuse = True
    with TestClient(app) as client:
        body = {key: field["value"] for key, field in _compute_fields(client).items()}
        body.update(max_sessions_per_machine=3, max_machine_age_seconds=3600)
        assert client.patch("/api/v1/forge/admin/settings/compute", json=body).status_code == 200

        fields = _compute_fields(client)

        assert fields["max_sessions_per_machine"]["label"] == "Sessions before replacement"
        assert fields["max_sessions_per_machine"]["type"] == "number"
        assert fields["max_machine_age_seconds"]["label"] == "Machine lifetime (seconds)"
        assert fields["max_machine_age_seconds"]["value"] == 3600


def test_more_slots_than_the_runtime_supports_is_refused_with_the_remedy(app, setup):
    setup[4].supports_slots = False
    with TestClient(app) as client:
        body = {key: field["value"] for key, field in _compute_fields(client).items()}
        body["slots_per_machine"] = 2

        response = client.patch("/api/v1/forge/admin/settings/compute", json=body)

        assert response.status_code == 422
        assert "slots_per_machine=2" in response.json()["detail"]
        assert "Sessions per machine" in response.json()["detail"]
        assert setup[2].policies["pool"].slots_per_machine == 1


def test_reuse_with_a_drain_limit_is_refused(app, setup):
    setup[4].supports_reuse = True
    with TestClient(app) as client:
        body = {key: field["value"] for key, field in _compute_fields(client).items()}
        body.update(reuse_policy="reuse", max_machine_age_seconds=60)

        response = client.patch("/api/v1/forge/admin/settings/compute", json=body)

        assert response.status_code == 422


async def test_status_lists_each_machines_sessions_and_only_empty_machines_can_be_disposed(
    app, setup
):
    pool, leases, repo, _, _, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    spare = await leases.acquire_standby(profile="small")
    with TestClient(app) as client:
        schema = client.get("/api/v1/forge/settings").json()
        sections = {s["id"]: s for s in schema["sections"]}

        status = {f["key"]: f for f in sections["compute-status"]["fields"]}
        assert status["counts"]["value"] == "ready: 2"
        assert status[str(claim.id)]["value"] == "ready · small · 1 of 1 slots"
        assert status[str(claim.id)]["description"] == (
            f"Slot 0 (starting): session {claim.session_id}"
        )
        assert status[str(spare.id)]["value"] == "ready · small · 0 of 1 slots"
        assert status[str(spare.id)]["description"] == "No sessions"
        # The spare machine's free slot counts towards the one warm slot the pool keeps.
        assert status["capacity_shortfall"]["label"] == "Warm capacity"
        assert status["capacity_shortfall"]["readOnly"]
        assert status["capacity_shortfall"]["value"] == "1 free warm slots; 1 wanted"
        dispose = sections["compute-dispose"]["fields"][0]
        assert [option["value"] for option in dispose["options"]] == ["", str(spare.id)]


async def test_a_failed_machine_shows_its_error_beside_its_sessions(app, setup):
    from volundr.domain.compute import LeaseState

    _, leases, repo, _, _, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    async with repo.operation(claim.id):
        await repo.save(
            repo.leases[claim.id].model_copy(update={"state": LeaseState.FAILED, "error": "boom"})
        )
    with TestClient(app) as client:
        sections = {s["id"]: s for s in client.get("/api/v1/forge/settings").json()["sections"]}

        status = {f["key"]: f for f in sections["compute-status"]["fields"]}

        assert status[str(claim.id)]["description"] == (
            f"boom; Slot 0 (starting): session {claim.session_id}"
        )


async def test_a_capacity_shortfall_is_shown_as_the_warm_capacity(app, setup):
    pool = setup[0]
    pool.capacity_shortfall = "0 of 1 free warm slots: the pool is at its maximum of 2 machines"
    with TestClient(app) as client:
        sections = {s["id"]: s for s in client.get("/api/v1/forge/settings").json()["sections"]}

        status = {f["key"]: f for f in sections["compute-status"]["fields"]}

        assert status["capacity_shortfall"]["value"] == pool.capacity_shortfall
