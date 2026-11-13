"""The memory import routes: the project owner's, with their own login."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from tests.test_ting.memory_import_fakes import PROJECT_ID, issue
from tests.test_ting.test_memory_imports import CONNECTION, Harness
from ting.adapters.inbound.auth import extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.memory_imports import create_memory_imports_router
from ting.domain.memory_imports import ImportConnectionError

PERSON = Principal(user_id="jozef", email="j@niuu.test", tenant_id="default", roles=[])
OTHER_PROJECT = UUID("22222222-2222-4222-8222-222222222222")


@dataclass
class FakeForge:
    roles: dict[str, str] = field(default_factory=dict)
    error: Exception | None = None
    asked: list[tuple[str, str | None]] = field(default_factory=list)

    async def get_project_role(self, project_id, *, auth_token=None, principal=None):  # noqa: ANN001
        self.asked.append((project_id, principal.user_id if principal else None))
        if self.error is not None:
            raise self.error
        return self.roles.get(project_id)


class FakeVolundrFactory:
    def __init__(self, *forges: FakeForge) -> None:
        self.forges = list(forges)

    async def for_principal(self, principal):  # noqa: ANN001
        return self.forges


class Api:
    def __init__(self, harness: Harness, forge: FakeForge | None = None) -> None:
        self.harness = harness
        self.forge = forge or FakeForge({str(PROJECT_ID): "owner"})
        self.principal = PERSON
        self.app = FastAPI()
        self.app.include_router(create_memory_imports_router())
        self.app.state.memory_import_service = harness.service
        self.app.dependency_overrides[extract_principal] = lambda: self.principal
        self.app.dependency_overrides[resolve_volundr_factory] = lambda: FakeVolundrFactory(
            self.forge
        )
        self.client = TestClient(self.app)

    def body(self, **changes) -> dict:  # noqa: ANN003
        return {
            "project_id": str(PROJECT_ID),
            "connection_id": CONNECTION,
            "tracker_project_id": "linear-project-1",
            "compile": True,
            **changes,
        }


@pytest.fixture
def api() -> Api:
    return Api(Harness(issue(1, minutes=10), issue(2, minutes=20)))


def test_the_owner_creates_an_import_and_lists_it(api: Api) -> None:
    created = api.client.post("/api/v1/ting/memory/imports", json=api.body())

    assert created.status_code == 201
    record = created.json()
    assert record["project_id"] == str(PROJECT_ID)
    assert record["owner_id"] == "jozef"
    assert record["connector"] == "linear"
    assert record["compile"] is True
    assert record["cursor"] is None
    assert record["last_error"] == ""
    listed = api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)})
    assert [item["id"] for item in listed.json()] == [record["id"]]
    assert (str(PROJECT_ID), "jozef") in api.forge.asked


def test_a_person_who_is_not_an_owner_is_refused_on_every_route(api: Api) -> None:
    created = api.client.post("/api/v1/ting/memory/imports", json=api.body())
    import_id = created.json()["id"]
    api.forge.roles[str(PROJECT_ID)] = "contributor"

    responses = [
        api.client.post("/api/v1/ting/memory/imports", json=api.body(tracker_project_id="x")),
        api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)}),
        api.client.delete(f"/api/v1/ting/memory/imports/{import_id}"),
        api.client.post(f"/api/v1/ting/memory/imports/{import_id}/run"),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    assert all("owner" in r.json()["detail"] for r in responses)
    assert api.harness.repository.records  # nothing was deleted or run
    assert api.harness.gateway.writes == []


def test_someone_not_on_the_project_learns_nothing_about_it(api: Api) -> None:
    created = api.client.post("/api/v1/ting/memory/imports", json=api.body())
    import_id = created.json()["id"]
    api.forge.roles.clear()

    responses = [
        api.client.post("/api/v1/ting/memory/imports", json=api.body()),
        api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)}),
        api.client.delete(f"/api/v1/ting/memory/imports/{import_id}"),
        api.client.post(f"/api/v1/ting/memory/imports/{import_id}/run"),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert {r.json()["detail"] for r in responses[:2]} == {"Project not found"}
    assert api.harness.repository.records


@pytest.mark.parametrize(
    "principal",
    [
        Principal("sess", "", "default", [], token_use="forge_session", scopes=("forge:notify",)),
        Principal("jozef", "", "default", [], scopes=("ting:workflow:launch",)),
        Principal("jozef", "", "default", [], actor="system:serviceaccount:ravn:regin"),
    ],
    ids=["session credential", "scoped token", "workload acting for a person"],
)
def test_only_a_persons_own_login_is_admitted(api: Api, principal: Principal) -> None:
    api.principal = principal

    responses = [
        api.client.post("/api/v1/ting/memory/imports", json=api.body()),
        api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)}),
        api.client.delete(f"/api/v1/ting/memory/imports/{uuid4()}"),
        api.client.post(f"/api/v1/ting/memory/imports/{uuid4()}/run"),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    assert api.forge.asked == []


def test_a_connection_that_is_not_the_projects_team_integration_is_refused(api: Api) -> None:
    api.harness.connections.refusal = ImportConnectionError(
        "integration connection conn is not a team integration of project"
    )

    response = api.client.post("/api/v1/ting/memory/imports", json=api.body())

    assert response.status_code == 422
    assert "team integration" in response.json()["detail"]
    assert api.harness.repository.records == {}


def test_a_duplicate_import_conflicts(api: Api) -> None:
    assert api.client.post("/api/v1/ting/memory/imports", json=api.body()).status_code == 201

    response = api.client.post("/api/v1/ting/memory/imports", json=api.body())

    assert response.status_code == 409


def test_an_unknown_connector_or_extra_field_is_a_validation_error(api: Api) -> None:
    assert (
        api.client.post("/api/v1/ting/memory/imports", json=api.body(connector="slack")).status_code
        == 422
    )
    assert (
        api.client.post("/api/v1/ting/memory/imports", json=api.body(surprise=1)).status_code == 422
    )
    body = api.body()
    del body["compile"]
    assert api.client.post("/api/v1/ting/memory/imports", json=body).status_code == 422


def test_deleting_removes_the_import(api: Api) -> None:
    import_id = api.client.post("/api/v1/ting/memory/imports", json=api.body()).json()["id"]

    deleted = api.client.delete(f"/api/v1/ting/memory/imports/{import_id}")

    assert deleted.status_code == 204
    assert api.harness.repository.records == {}
    assert api.client.delete(f"/api/v1/ting/memory/imports/{import_id}").status_code == 404


def test_run_now_imports_and_reports_what_it_did(api: Api) -> None:
    import_id = api.client.post("/api/v1/ting/memory/imports", json=api.body()).json()["id"]

    response = api.client.post(f"/api/v1/ting/memory/imports/{import_id}/run")

    assert response.status_code == 200
    answer = response.json()
    assert (answer["read"], answer["new_sources"], answer["unchanged"]) == (2, 2, 0)
    assert answer["compile_run"] == "run-1"
    assert len(answer["new_source_ids"]) == 2
    assert answer["cursor"].startswith("2026-11-10T12:20:00")
    again = api.client.post(f"/api/v1/ting/memory/imports/{import_id}/run").json()
    assert (again["read"], again["new_sources"], again["compile_run"]) == (0, 0, None)


def test_a_failed_run_now_is_a_bad_gateway_that_names_the_failure_and_is_recorded(
    api: Api,
) -> None:
    import_id = api.client.post("/api/v1/ting/memory/imports", json=api.body()).json()["id"]
    api.harness.gateway.fail_with("LIN-1", "the gateway refused Ting's credential")

    response = api.client.post(f"/api/v1/ting/memory/imports/{import_id}/run")

    assert response.status_code == 502
    assert "the gateway refused Ting's credential" in response.json()["detail"]
    listed = api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)})
    assert "refused" in listed.json()[0]["last_error"]
    assert listed.json()[0]["cursor"] is None


def test_an_unknown_import_is_not_found(api: Api) -> None:
    assert api.client.delete(f"/api/v1/ting/memory/imports/{uuid4()}").status_code == 404
    assert api.client.post(f"/api/v1/ting/memory/imports/{uuid4()}/run").status_code == 404


def test_a_forge_that_cannot_answer_is_a_bad_gateway(api: Api) -> None:
    api.forge.error = httpx.ConnectError("forge is down")

    response = api.client.get(
        "/api/v1/ting/memory/imports", params={"project_id": str(OTHER_PROJECT)}
    )

    assert response.status_code == 502
    assert "Forge could not tell your role" in response.json()["detail"]


def test_without_a_forge_connection_the_routes_say_so(api: Api) -> None:
    api.app.dependency_overrides[resolve_volundr_factory] = lambda: FakeVolundrFactory()

    response = api.client.get("/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)})

    assert response.status_code == 503


def test_the_project_is_asked_of_every_forge_the_person_has() -> None:
    harness = Harness()
    here = FakeForge()
    there = FakeForge({str(PROJECT_ID): "owner"})
    api = Api(harness)
    api.app.dependency_overrides[resolve_volundr_factory] = lambda: FakeVolundrFactory(here, there)

    response = api.client.post("/api/v1/ting/memory/imports", json=api.body())

    assert response.status_code == 201
    assert [forge.asked for forge in (here, there)] == [
        [(str(PROJECT_ID), "jozef")],
        [(str(PROJECT_ID), "jozef")],
    ]


def test_the_routes_say_when_imports_are_not_enabled() -> None:
    app = FastAPI()
    app.include_router(create_memory_imports_router())
    app.dependency_overrides[extract_principal] = lambda: PERSON
    app.dependency_overrides[resolve_volundr_factory] = lambda: FakeVolundrFactory(FakeForge())

    response = TestClient(app).get(
        "/api/v1/ting/memory/imports", params={"project_id": str(PROJECT_ID)}
    )

    assert response.status_code == 503
    assert "memory_imports.enabled" in response.json()["detail"]
