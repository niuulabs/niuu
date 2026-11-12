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
