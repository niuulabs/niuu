"""ForgeSessionDirectory: contract C1 over HTTP, every failure a refusal."""

from __future__ import annotations

import httpx
import pytest
import respx

from mimir.adapters.forge_directory import ForgeSessionDirectory
from mimir.ports.session_directory import SessionNotConfirmedError
from tests.test_mimir.unit.memory_fakes import PROJECT_ID, SESSION_ID

FORGE = "http://forge.test"
URL = f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}/memory-context"


def _body(**overrides) -> dict:
    return {
        "session_id": SESSION_ID,
        "launch_id": "launch-1",
        "owner_id": "ines",
        "tenant_id": "acme",
        "project_id": PROJECT_ID,
        "workflow_run_id": None,
        "project_memory": {"write_prefixes": ["decisions/", "runbooks/"]},
        "model": "claude-fable-5-1",
        "objective": None,
        "project_brief": None,
        "since": None,
        "attached": [],
        **overrides,
    }


@pytest.fixture
async def directory():
    found = ForgeSessionDirectory(FORGE + "/", timeout_seconds=5)
    yield found
    await found.close()


@respx.mock
async def test_forge_is_asked_with_the_callers_own_credential(directory) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=_body()))
    found = await directory.memory_context(SESSION_ID, "tok-123")
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-123"
    assert found.owner_id == "ines"
    assert found.tenant_id == "acme"
    assert found.project_id == PROJECT_ID
    assert found.project_write_prefixes == ("decisions/", "runbooks/")


@respx.mock
async def test_a_session_without_a_project(directory) -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, json=_body(project_id=None, project_memory=None, workflow_run_id="run-7")
        )
    )
    found = await directory.memory_context(SESSION_ID, "tok")
    assert found.project_id is None and found.project_write_prefixes == ()
    assert found.workflow_run_id == "run-7"


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
@respx.mock
async def test_any_answer_but_200_is_a_refusal(directory, status) -> None:
    respx.get(URL).mock(return_value=httpx.Response(status, json={"detail": "no"}))
    with pytest.raises(SessionNotConfirmedError, match=str(status)):
        await directory.memory_context(SESSION_ID, "tok")


@respx.mock
async def test_an_unreachable_forge_is_a_refusal(directory) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SessionNotConfirmedError, match="could not be asked"):
        await directory.memory_context(SESSION_ID, "tok")


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (_body(session_id="someone-else"), "answered for session"),
        (_body(owner_id=""), "owner_id"),
        (_body(tenant_id=None), "tenant_id"),
        (_body(project_memory=None), "write_prefixes"),
        (_body(project_id=""), "project_id"),
        (["not", "an", "object"], "not an object"),
    ],
)
@respx.mock
async def test_a_malformed_answer_is_a_refusal(directory, body, match) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SessionNotConfirmedError, match=match):
        await directory.memory_context(SESSION_ID, "tok")


@respx.mock
async def test_a_non_json_answer_is_a_refusal(directory) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(SessionNotConfirmedError, match="not JSON"):
        await directory.memory_context(SESSION_ID, "tok")


def test_the_forge_url_is_required() -> None:
    with pytest.raises(ValueError, match="memory.forge_base_url"):
        ForgeSessionDirectory(" ", timeout_seconds=5)
    with pytest.raises(ValueError, match="positive"):
        ForgeSessionDirectory(FORGE, timeout_seconds=0)


@respx.mock
async def test_memory_context_v2_fields_are_read(directory) -> None:
    body = _body(
        workflow_run_id="A1B2C3D4-0000-4000-8000-000000000123",
        objective="Change the booking cut-over",
        project_brief="Booking platform",
        since="2026-11-01T09:00:00Z",
        attached=[
            {
                "name": "team_notes",
                "registry_entry_id": "mimir-notes",
                "access": "read_write",
                "write_prefixes": ["notes/"],
                "source": "project",
            },
            {"name": "vendor", "registry_entry_id": "mimir-vendor", "access": "read"},
        ],
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    found = await directory.memory_context(SESSION_ID, "tok")
    assert found.model == "claude-fable-5-1"
    assert found.workflow_run_id == "a1b2c3d4-0000-4000-8000-000000000123"
    assert found.objective == "Change the booking cut-over"
    assert found.since is not None and found.since.isoformat() == "2026-11-01T09:00:00+00:00"
    assert [(a.name, a.access.value, a.write_prefixes) for a in found.attached] == [
        ("team_notes", "read_write", ("notes/",)),
        ("vendor", "read", ()),
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"attached": None},
        {"attached": [{"name": "Bad Name", "registry_entry_id": "x", "access": "read"}]},
        {"attached": [{"name": "notes", "registry_entry_id": "", "access": "read"}]},
        {"attached": [{"name": "notes", "registry_entry_id": "x", "access": "write"}]},
        {
            "attached": [
                {
                    "name": "notes",
                    "registry_entry_id": "x",
                    "access": "read",
                    "write_prefixes": "notes/",
                }
            ]
        },
        {"since": "yesterday"},
        {"model": 7},
    ],
)
@respx.mock
async def test_a_malformed_v2_answer_is_a_refusal(directory, overrides) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=_body(**overrides)))
    with pytest.raises(SessionNotConfirmedError):
        await directory.memory_context(SESSION_ID, "tok")


@respx.mock
async def test_a_step_1_answer_without_attached_is_a_refusal_naming_c4(directory) -> None:
    body = _body()
    del body["attached"]
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SessionNotConfirmedError, match="C4"):
        await directory.memory_context(SESSION_ID, "tok")
