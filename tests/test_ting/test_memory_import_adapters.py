"""The adapters of a memory import: Linear, the gateway route, team connections and storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import httpx
import pytest
import respx

from niuu.adapters.linear import GraphQLError
from niuu.domain.models import IntegrationConnection, IntegrationType, project_owner_id
from tests.test_ting.conftest import StubCredentialStore, StubIntegrationRepo
from tests.test_ting.memory_import_fakes import BASE_TIME, PROJECT_ID, issue
from tests.test_ting.test_team_tracker import RecordingUsageLog
from ting.adapters.linear import LinearTrackerAdapter
from ting.adapters.memory_gateway_http import HttpMemorySourceGateway
from ting.adapters.postgres_memory_imports import PostgresMemoryImportRepository
from ting.adapters.team_tracker import TeamIssueReader
from ting.adapters.tracker_factory import TrackerAdapterFactory
from ting.domain.memory_imports import (
    ImportConnectionError,
    MemoryImport,
    MemoryImportExistsError,
    SourceGatewayError,
    SourceWrite,
)

LINEAR_URL = "https://test.linear.app/graphql"
GATEWAY = "http://mimir.test/api/v1/mimir"
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


# -- Linear: tickets updated since, with their comments -------------------------------


def _node(number: int, updated: str) -> dict:
    return {
        "id": f"uuid-{number}",
        "identifier": f"LIN-{number}",
        "title": f"Ticket {number}",
        "description": f"Body {number}",
        "state": {"name": "In Progress"},
        "assignee": {"name": "Ines Moreau"},
        "labels": {"nodes": [{"name": "backend"}]},
        "url": f"https://linear.app/niuu/issue/LIN-{number}",
        "createdAt": "2026-11-01T08:00:00.000Z",
        "updatedAt": updated,
    }


class FakeLinear:
    """Linear's GraphQL API for the two queries an import makes."""

    def __init__(self) -> None:
        #: Pages of ListIssuesUpdated nodes, in order.
        self.issue_pages: list[list[dict]] = []
        #: Pages of comments per issue uuid.
        self.comment_pages: dict[str, list[list[dict]]] = {}
        self.requests: list[dict] = []
        self.authorization: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.authorization.append(request.headers.get("authorization", ""))
        variables = body["variables"]
        if "ListIssuesUpdated" in body["query"]:
            index = int(variables["after"] or 0)
            nodes = self.issue_pages[index]
            more = index + 1 < len(self.issue_pages)
            data = {
                "issues": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": more, "endCursor": str(index + 1)},
                }
            }
            return httpx.Response(200, json={"data": data})
        pages = self.comment_pages.get(variables["id"], [[]])
        index = int(variables["after"] or 0)
        more = index + 1 < len(pages)
        comments = {
            "nodes": pages[index],
            "pageInfo": {"hasNextPage": more, "endCursor": str(index + 1)},
        }
        return httpx.Response(200, json={"data": {"issue": {"comments": comments}}})


def _adapter() -> LinearTrackerAdapter:
    return LinearTrackerAdapter(api_key="lin_api_test", api_url=LINEAR_URL)


@respx.mock
async def test_linear_reads_every_page_of_tickets_updated_after_the_cursor_with_all_comments() -> (
    None
):
    linear = FakeLinear()
    linear.issue_pages = [
        [_node(2, "2026-11-10T12:20:00.000Z")],
        [_node(1, "2026-11-10T12:10:00.000Z")],
    ]
    linear.comment_pages = {
        "uuid-1": [
            [
                {
                    "id": "c2",
                    "body": "Second",
                    "createdAt": "2026-11-10T11:00:00.000Z",
                    "user": {"name": "Ines Moreau"},
                }
            ],
            [
                {
                    "id": "c1",
                    "body": "First",
                    "createdAt": "2026-11-09T11:00:00.000Z",
                    "user": None,
                }
            ],
        ],
        "uuid-2": [[]],
    }
    respx.route(method="POST", host="test.linear.app").mock(side_effect=linear)
    adapter = _adapter()

    issues = await adapter.issues_updated_since("linear-project-1", BASE_TIME)
    await adapter.close()

    assert [item.identifier for item in issues] == ["LIN-1", "LIN-2"]  # oldest change first
    first = issues[0]
    assert first.url == "https://linear.app/niuu/issue/LIN-1"
    assert first.status == "In Progress"
    assert first.labels == ("backend",)
    assert first.assignee == "Ines Moreau"
    assert first.updated_at == datetime(2026, 11, 10, 12, 10, tzinfo=UTC)
    assert [(c.body, c.author) for c in first.comments] == [
        ("First", ""),
        ("Second", "Ines Moreau"),
    ]
    assert issues[1].comments == ()
    listing = [r for r in linear.requests if "ListIssuesUpdated" in r["query"]]
    assert len(listing) == 2
    assert listing[0]["variables"]["filter"] == {
        "project": {"id": {"eq": "linear-project-1"}},
        "updatedAt": {"gt": BASE_TIME.isoformat()},
    }
    assert linear.authorization[0] == "lin_api_test"


@respx.mock
async def test_linear_without_a_cursor_reads_the_whole_project() -> None:
    linear = FakeLinear()
    linear.issue_pages = [[_node(1, "2026-11-10T12:10:00.000Z")]]
    respx.route(method="POST", host="test.linear.app").mock(side_effect=linear)

    await _adapter().issues_updated_since("linear-project-1", None)

    assert linear.requests[0]["variables"]["filter"] == {
        "project": {"id": {"eq": "linear-project-1"}}
    }


@respx.mock
async def test_linear_pagination_without_a_cursor_is_an_error() -> None:
    respx.route(method="POST", host="test.linear.app").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": True}}}},
        )
    )

    with pytest.raises(GraphQLError, match="endCursor"):
        await _adapter().issues_updated_since("p", None)


@respx.mock
async def test_linear_failures_are_raised_not_swallowed() -> None:
    respx.route(method="POST", host="test.linear.app").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _adapter().issues_updated_since("p", None)


@respx.mock
async def test_linear_refuses_a_timestamp_without_a_timezone() -> None:
    linear = FakeLinear()
    linear.issue_pages = [[_node(1, "2026-11-10T12:10:00")]]
    respx.route(method="POST", host="test.linear.app").mock(side_effect=linear)

    with pytest.raises(GraphQLError, match="timezone"):
        await _adapter().issues_updated_since("p", None)


# -- The project-keyed team connection ------------------------------------------------


def _team_connection(**overrides) -> IntegrationConnection:  # noqa: ANN003
    values = {
        "id": "conn-team-linear",
        "owner_id": project_owner_id(PROJECT_ID),
        "project_id": str(PROJECT_ID),
        "integration_type": IntegrationType.ISSUE_TRACKER,
        "adapter": "ting.adapters.linear.LinearTrackerAdapter",
        "credential_name": "linear-team",
        "config": {"api_url": LINEAR_URL},
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "slug": "linear",
        "connected_by": "ines",
    }
    return IntegrationConnection(**{**values, **overrides})


def _factory(
    *connections: IntegrationConnection,
    usage: RecordingUsageLog | None = None,
    credentials: dict | None = None,
) -> tuple[TrackerAdapterFactory, RecordingUsageLog]:
    log = usage or RecordingUsageLog()
    store = StubCredentialStore(
        credentials
        if credentials is not None
        else {f"project:{PROJECT_ID}:linear-team": {"token": "lin_api_team"}}
    )
    return (
        TrackerAdapterFactory(StubIntegrationRepo(list(connections)), store, usage_log=log),
        log,
    )


@respx.mock
async def test_a_team_connection_reads_with_the_projects_credential_and_records_each_read() -> None:
    linear = FakeLinear()
    linear.issue_pages = [[_node(1, "2026-11-10T12:10:00.000Z")]]
    respx.route(method="POST", host="test.linear.app").mock(side_effect=linear)
    factory, log = _factory(_team_connection())

    reader = await factory.reader(PROJECT_ID, "conn-team-linear", acting_for="jozef")
    issues = await reader.issues_updated_since("linear-project-1", None)
    await reader.close()

    assert isinstance(reader, TeamIssueReader)
    assert [item.identifier for item in issues] == ["LIN-1"]
    assert linear.authorization[0] == "lin_api_team"
    assert len(log.recorded) == 1
    usage = log.recorded[0]
    assert (usage.action, usage.actor, usage.acting_for) == ("import", "ting", "jozef")
    assert (usage.connection_id, usage.project_id) == ("conn-team-linear", str(PROJECT_ID))
    assert usage.subject == "linear-project-1"


async def test_a_read_whose_use_cannot_be_recorded_does_not_happen() -> None:
    factory, _ = _factory(_team_connection(), usage=RecordingUsageLog(fail=True))
    reader = await factory.reader(PROJECT_ID, "conn-team-linear", acting_for="jozef")

    with pytest.raises(ConnectionError, match="database down"):
        await reader.issues_updated_since("linear-project-1", None)


@pytest.mark.parametrize(
    ("connections", "credentials", "message"),
    [
        ([], None, "does not exist"),
        (
            [_team_connection(owner_id="jozef", project_id=None)],
            None,
            "not a team integration of project",
        ),
        (
            [
                _team_connection(
                    project_id="33333333-3333-4333-8333-333333333333",
                    owner_id="project:33333333-3333-4333-8333-333333333333",
                )
            ],
            None,
            "not a team integration of project",
        ),
        ([_team_connection(integration_type=IntegrationType.MESSAGING)], None, "not an issue"),
        ([_team_connection(enabled=False)], None, "disabled"),
        (
            [_team_connection(adapter="volundr.adapters.outbound.jira.JiraAdapter")],
            None,
            "not a linear connection",
        ),
        ([_team_connection()], {}, "credential 'linear-team' of team integration"),
    ],
    ids=[
        "missing",
        "a person's own connection",
        "another project's",
        "not a tracker",
        "disabled",
        "not linear",
        "no credential",
    ],
)
async def test_a_connection_that_is_not_usable_for_the_project_is_refused_with_the_fix(
    connections: list[IntegrationConnection],
    credentials: dict | None,
    message: str,
) -> None:
    factory, log = _factory(*connections, credentials=credentials)

    with pytest.raises(ImportConnectionError, match=f"(?i){message}"):
        await factory.reader(PROJECT_ID, "conn-team-linear", acting_for="jozef")

    assert log.recorded == []


async def test_a_team_connection_cannot_be_used_without_a_usage_log() -> None:
    factory = TrackerAdapterFactory(
        StubIntegrationRepo([_team_connection()]),
        StubCredentialStore({f"project:{PROJECT_ID}:linear-team": {"token": "t"}}),
    )

    with pytest.raises(ImportConnectionError, match="usage log"):
        await factory.reader(PROJECT_ID, "conn-team-linear", acting_for="jozef")


async def test_an_adapter_that_cannot_read_updates_cannot_feed_an_import() -> None:
    factory, _ = _factory(
        _team_connection(adapter="tests.test_ting.test_tracker_factory.FakeTracker")
    )

    with pytest.raises(ImportConnectionError, match="not a linear connection"):
        await factory.reader(PROJECT_ID, "conn-team-linear", acting_for="jozef")


# -- The gateway route ----------------------------------------------------------------


class _Auth:
    def __init__(self) -> None:
        self.invalidated = 0

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer ting-service"}

    def invalidate(self) -> bool:
        self.invalidated += 1
        return True


def _write() -> SourceWrite:
    ticket = issue(482, minutes=5)
    return SourceWrite(
        tenant_id="default",
        layer=f"project:{PROJECT_ID}",
        connector="linear",
        external_id=ticket.identifier,
        title="LIN-482 Booking cut-over plan",
        content="the ticket text",
        kind="ticket",
        origin_url=ticket.url,
        fetched_at=NOW,
        upstream_updated_at=ticket.updated_at,
    )


@respx.mock
async def test_the_gateway_is_called_with_the_contracts_body_and_the_service_credential() -> None:
    route = respx.post(f"{GATEWAY}/memory/sources").mock(
        return_value=httpx.Response(
            200, json={"source_id": "src-1", "layer": f"project:{PROJECT_ID}", "new": True}
        )
    )
    gateway = HttpMemorySourceGateway(base_url=f"{GATEWAY}/", auth=_Auth(), timeout_seconds=5)

    accepted = await gateway.write_source(_write())

    assert (accepted.source_id, accepted.layer, accepted.new) == (
        "src-1",
        f"project:{PROJECT_ID}",
        True,
    )
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer ting-service"
    assert json.loads(request.content) == {
        "tenant_id": "default",
        "layer": f"project:{PROJECT_ID}",
        "connector": "linear",
        "external_id": "LIN-482",
        "title": "LIN-482 Booking cut-over plan",
        "content": "the ticket text",
        "kind": "ticket",
        "origin_url": "https://linear.app/niuu/issue/LIN-482",
        "fetched_at": NOW.isoformat(),
        "upstream_updated_at": "2026-11-10T12:05:00+00:00",
    }


@respx.mock
async def test_a_refused_credential_is_discarded_and_raised() -> None:
    respx.post(f"{GATEWAY}/memory/sources").mock(return_value=httpx.Response(403, text="forbidden"))
    auth = _Auth()
    gateway = HttpMemorySourceGateway(base_url=GATEWAY, auth=auth, timeout_seconds=5)

    with pytest.raises(SourceGatewayError, match="connector_service_subjects"):
        await gateway.write_source(_write())

    assert auth.invalidated == 1


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, text="layer must be project or org"),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"source_id": "s", "layer": "l"}),
        httpx.Response(200, json={"source_id": "s", "layer": "l", "new": "yes"}),
        httpx.Response(200, json=["nope"]),
    ],
    ids=["400", "500", "not json", "missing new", "new is not a bool", "not an object"],
)
async def test_anything_but_an_accepted_source_is_an_error(response: httpx.Response) -> None:
    respx.post(f"{GATEWAY}/memory/sources").mock(return_value=response)
    gateway = HttpMemorySourceGateway(base_url=GATEWAY, auth=_Auth(), timeout_seconds=5)

    with pytest.raises(SourceGatewayError, match="LIN-482"):
        await gateway.write_source(_write())


@respx.mock
async def test_an_unreachable_gateway_is_an_error_naming_the_setting() -> None:
    respx.post(f"{GATEWAY}/memory/sources").mock(side_effect=httpx.ConnectError("refused"))
    gateway = HttpMemorySourceGateway(base_url=GATEWAY, auth=_Auth(), timeout_seconds=5)

    with pytest.raises(SourceGatewayError, match="memory_imports.gateway_url"):
        await gateway.write_source(_write())


def test_a_gateway_without_a_url_or_timeout_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="memory_imports.gateway_url"):
        HttpMemorySourceGateway(base_url=" ", auth=_Auth(), timeout_seconds=5)
    with pytest.raises(ValueError, match="gateway_timeout_seconds"):
        HttpMemorySourceGateway(base_url=GATEWAY, auth=_Auth(), timeout_seconds=0)


# -- Storage --------------------------------------------------------------------------


def _record() -> MemoryImport:
    return MemoryImport(
        id=uuid4(),
        project_id=PROJECT_ID,
        tenant_id="default",
        owner_id="jozef",
        connector="linear",
        connection_id="conn-team-linear",
        tracker_project_id="linear-project-1",
        compile=True,
        cursor=None,
        created_at=NOW,
    )


def _row(record: MemoryImport) -> dict:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "tenant_id": record.tenant_id,
        "owner_id": record.owner_id,
        "connector": record.connector,
        "connection_id": record.connection_id,
        "tracker_project_id": record.tracker_project_id,
        "compile": record.compile,
        "cursor": record.cursor,
        "created_at": record.created_at,
        "last_run_at": None,
        "last_error": "",
        "pending_source_ids": ["src-1"],
    }


async def test_an_import_is_stored_and_read_back_with_its_pending_sources() -> None:
    record = _record()
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_row(record))
    pool.fetch = AsyncMock(return_value=[_row(record)])
    repo = PostgresMemoryImportRepository(pool)

    await repo.create(record)
    read = await repo.get(record.id)
    listed = await repo.list_for_project(PROJECT_ID)
    everything = await repo.list_all()

    assert "INSERT INTO memory_imports" in pool.execute.await_args.args[0]
    assert pool.execute.await_args.args[1:3] == (record.id, PROJECT_ID)
    assert read.pending_source_ids == ("src-1",)
    assert listed == everything == [read]
    assert pool.fetch.await_args_list[0].args[1] == PROJECT_ID


async def test_a_duplicate_source_is_a_conflict_that_names_the_fix() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate"))

    with pytest.raises(MemoryImportExistsError, match="delete that import"):
        await PostgresMemoryImportRepository(pool).create(_record())


async def test_the_cursor_only_moves_forward_and_a_missing_import_reads_as_none() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    repo = PostgresMemoryImportRepository(pool)

    await repo.record_progress(uuid4(), cursor=BASE_TIME, add_pending=["a"])
    await repo.clear_pending(uuid4(), ["a"])
    await repo.record_run(uuid4(), ran_at=NOW, error="boom")

    progress, clear, run = (call.args for call in pool.execute.await_args_list)
    assert "GREATEST(cursor, $2::timestamptz)" in progress[0]
    assert progress[2:] == (BASE_TIME, ["a"])
    assert "source_id <> ALL($2::text[])" in clear[0]
    assert run[2:] == (NOW, "boom")
    assert await repo.get(uuid4()) is None


async def test_deleting_says_whether_there_was_an_import() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[{"id": uuid4()}, None])
    repo = PostgresMemoryImportRepository(pool)

    assert await repo.delete(uuid4()) is True
    assert await repo.delete(uuid4()) is False


def test_the_migration_creates_and_drops_the_table() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "migrations" / "ting"
    up = (root / "000062_memory_imports.up.sql").read_text()
    down = (root / "000062_memory_imports.down.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS memory_imports" in up
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in up
    assert "DROP TABLE IF EXISTS memory_imports" in down
    for column in (
        "project_id",
        "tenant_id",
        "owner_id",
        "connector",
        "connection_id",
        "tracker_project_id",
        "compile",
        "cursor",
        "created_at",
        "last_run_at",
        "last_error",
    ):
        assert column in up
