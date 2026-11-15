"""A memory import's run: what it reads, what it hands the gateway, and when it compiles."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_ting.memory_import_fakes import (
    BASE_TIME,
    PROJECT_ID,
    FakeCompiler,
    FakeConnections,
    FakeGateway,
    FakeTracker,
    MemoryRepository,
    issue,
)
from ting.domain.memory_imports import (
    COMPILE_PROMPT,
    CompileLaunchError,
    ImportConnectionError,
    IssueComment,
    MemoryImportError,
    SourceGatewayError,
    issue_source_content,
)
from ting.domain.services.memory_imports import MemoryImportNotFoundError, MemoryImportService

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
CONNECTION = "conn-team-linear"


class Harness:
    def __init__(self, *issues) -> None:  # noqa: ANN002
        self.tracker = FakeTracker(*issues)
        self.connections = FakeConnections(self.tracker)
        self.gateway = FakeGateway()
        self.compiler = FakeCompiler()
        self.repository = MemoryRepository()
        self.service = MemoryImportService(
            repository=self.repository,
            connections=self.connections,
            gateway=self.gateway,
            compiler=self.compiler,
            clock=lambda: NOW,
        )

    async def create(self, *, compile: bool = True):
        return await self.service.create(
            project_id=PROJECT_ID,
            tenant_id="default",
            owner_id="jozef",
            connector="linear",
            connection_id=CONNECTION,
            tracker_project_id="linear-project-1",
            compile=compile,
        )

    async def record(self, import_id):
        return await self.repository.get(import_id)


@pytest.fixture
def two_issues() -> Harness:
    return Harness(issue(1, minutes=10), issue(2, minutes=20))


async def test_a_run_imports_both_tickets_and_compiles_the_new_sources(two_issues: Harness) -> None:
    record = await two_issues.create()

    result = await two_issues.service.run(record.id)

    assert (result.read, result.new_sources, result.unchanged) == (2, 2, 0)
    assert [w.external_id for w in two_issues.gateway.writes] == ["LIN-1", "LIN-2"]
    first = two_issues.gateway.writes[0]
    assert first.layer == f"project:{PROJECT_ID}"
    assert first.tenant_id == "default"
    assert first.connector == "linear"
    assert first.kind == "ticket"
    assert first.title == "LIN-1 Ticket 1"
    assert first.origin_url == "https://linear.app/niuu/issue/LIN-1"
    assert first.upstream_updated_at == BASE_TIME + timedelta(minutes=10)
    assert first.fetched_at == NOW
    assert result.cursor == BASE_TIME + timedelta(minutes=20)
    stored = await two_issues.record(record.id)
    assert stored.cursor == result.cursor
    assert stored.last_run_at == NOW
    assert stored.last_error == ""
    assert stored.pending_source_ids == ()
    assert len(two_issues.compiler.launched) == 1
    project_id, tenant_id, owner_id, source_ids = two_issues.compiler.launched[0]
    assert (project_id, tenant_id, owner_id) == (PROJECT_ID, "default", "jozef")
    assert source_ids == list(result.new_source_ids)
    assert result.compile_run == "run-1"


async def test_the_read_is_the_projects_team_connection_for_the_importing_person(
    two_issues: Harness,
) -> None:
    record = await two_issues.create()

    await two_issues.service.run(record.id)

    assert (PROJECT_ID, CONNECTION, "jozef") in two_issues.connections.asked
    # Every reader is closed: the one that checked the connection and the one that read.
    assert two_issues.tracker.closed == 2


async def test_a_rerun_imports_nothing_new_and_compiles_nothing(two_issues: Harness) -> None:
    record = await two_issues.create()
    await two_issues.service.run(record.id)
    two_issues.compiler.launched.clear()
    two_issues.gateway.writes.clear()

    result = await two_issues.service.run(record.id)

    assert (result.read, result.new_sources, result.unchanged) == (0, 0, 0)
    assert two_issues.tracker.reads[-1] == BASE_TIME + timedelta(minutes=20)
    assert two_issues.gateway.writes == []
    assert two_issues.compiler.launched == []
    assert result.compile_run is None


async def test_a_ticket_the_gateway_already_holds_is_read_but_not_compiled() -> None:
    harness = Harness(issue(1, minutes=10))
    first = await harness.create()
    await harness.service.run(first.id)
    harness.compiler.launched.clear()
    # The same content comes back (the cursor was lost): the gateway says it is not new.
    harness.repository.records[first.id] = replace(
        harness.repository.records[first.id], cursor=None
    )

    result = await harness.service.run(first.id)

    assert (result.read, result.new_sources, result.unchanged) == (1, 0, 1)
    assert harness.compiler.launched == []


async def test_a_changed_ticket_is_imported_again_and_only_it_is_compiled(
    two_issues: Harness,
) -> None:
    record = await two_issues.create()
    await two_issues.service.run(record.id)
    two_issues.compiler.launched.clear()
    two_issues.tracker.put(
        issue(
            2,
            minutes=45,
            comments=(IssueComment("Ines Moreau", "Cut-over moved to Friday", BASE_TIME),),
        )
    )

    result = await two_issues.service.run(record.id)

    assert (result.read, result.new_sources) == (1, 1)
    assert two_issues.gateway.writes[-1].external_id == "LIN-2"
    assert "Cut-over moved to Friday" in two_issues.gateway.writes[-1].content
    assert two_issues.compiler.launched[0][3] == list(result.new_source_ids)
    assert len(result.new_source_ids) == 1
    assert (await two_issues.record(record.id)).cursor == BASE_TIME + timedelta(minutes=45)


async def test_a_gateway_failure_keeps_the_cursor_and_records_the_error(
    two_issues: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    record = await two_issues.create()
    two_issues.gateway.fail_with("LIN-1", "the gateway refused Ting's credential")

    with caplog.at_level(logging.ERROR), pytest.raises(SourceGatewayError, match="refused"):
        await two_issues.service.run(record.id)

    stored = await two_issues.record(record.id)
    assert stored.cursor is None
    assert "SourceGatewayError: the gateway refused Ting's credential" in stored.last_error
    assert stored.last_run_at == NOW
    assert two_issues.compiler.launched == []
    assert any(
        r.levelno == logging.ERROR and "will run again next interval" in r.getMessage()
        for r in caplog.records
    )

    # It runs again next interval and picks up from the same place.
    two_issues.gateway.failing.clear()
    result = await two_issues.service.run(record.id)
    assert result.new_sources == 2
    assert (await two_issues.record(record.id)).last_error == ""


async def test_a_failure_part_way_moves_the_cursor_only_over_accepted_tickets(
    two_issues: Harness,
) -> None:
    record = await two_issues.create()
    two_issues.gateway.fail_with("LIN-2")

    with pytest.raises(SourceGatewayError):
        await two_issues.service.run(record.id)

    stored = await two_issues.record(record.id)
    assert stored.cursor == BASE_TIME + timedelta(minutes=10)
    assert len(stored.pending_source_ids) == 1
    assert two_issues.compiler.launched == []

    two_issues.gateway.failing.clear()
    result = await two_issues.service.run(record.id)

    assert result.read == 1
    assert result.new_sources == 1
    # The first run's source was waiting for its compile; this run compiles both.
    assert len(two_issues.compiler.launched) == 1
    assert len(two_issues.compiler.launched[0][3]) == 2


async def test_tickets_changed_at_the_same_moment_are_not_skipped_by_a_failure() -> None:
    harness = Harness(issue(1, minutes=10), issue(2, minutes=10), issue(3, minutes=10))
    record = await harness.create()
    harness.gateway.fail_with("LIN-3")

    with pytest.raises(SourceGatewayError):
        await harness.service.run(record.id)

    # Nothing older than the failing group was accepted, so the cursor must stay before it.
    assert (await harness.record(record.id)).cursor is None


async def test_compile_is_started_only_when_the_import_asks_for_it() -> None:
    harness = Harness(issue(1, minutes=10))
    record = await harness.create(compile=False)

    result = await harness.service.run(record.id)

    assert result.new_sources == 1
    assert harness.compiler.launched == []
    assert result.compile_run is None
    assert (await harness.record(record.id)).pending_source_ids == ()


async def test_a_compile_that_cannot_start_is_retried_with_its_sources() -> None:
    harness = Harness(issue(1, minutes=10))
    record = await harness.create()
    harness.compiler.error = CompileLaunchError("the owner has no Forge connection")

    with pytest.raises(CompileLaunchError):
        await harness.service.run(record.id)

    stored = await harness.record(record.id)
    assert stored.cursor == BASE_TIME + timedelta(minutes=10)
    assert len(stored.pending_source_ids) == 1
    assert "CompileLaunchError" in stored.last_error

    harness.compiler.error = None
    result = await harness.service.run(record.id)

    assert result.read == 0
    assert result.compile_run == "run-1"
    assert harness.compiler.launched[0][3] == list(stored.pending_source_ids)
    assert (await harness.record(record.id)).pending_source_ids == ()


async def test_a_tracker_failure_is_recorded_and_raised() -> None:
    harness = Harness(issue(1, minutes=10))
    record = await harness.create()
    harness.tracker.fail = RuntimeError("Linear answered 502")

    with pytest.raises(RuntimeError, match="502"):
        await harness.service.run(record.id)

    stored = await harness.record(record.id)
    assert stored.last_error == "RuntimeError: Linear answered 502"
    assert stored.cursor is None
    assert harness.gateway.writes == []


async def test_a_ticket_without_a_link_to_its_original_is_refused() -> None:
    harness = Harness(issue(1, minutes=10, url=""))
    record = await harness.create()

    with pytest.raises(MemoryImportError, match="no URL"):
        await harness.service.run(record.id)

    assert harness.gateway.writes == []


async def test_run_all_runs_every_import_and_counts_the_failures() -> None:
    good = Harness(issue(1, minutes=10))
    first = await good.create()
    other = await good.service.create(
        project_id=PROJECT_ID,
        tenant_id="default",
        owner_id="jozef",
        connector="linear",
        connection_id=CONNECTION,
        tracker_project_id="linear-project-2",
        compile=False,
    )
    good.gateway.fail_with("LIN-1")

    succeeded, failed = await good.service.run_all()

    assert (succeeded, failed) == (0, 2)
    assert (await good.record(first.id)).last_error
    assert (await good.record(other.id)).last_error

    good.gateway.failing.clear()
    assert await good.service.run_all() == (2, 0)


async def test_run_all_skips_an_import_deleted_since_it_was_listed() -> None:
    harness = Harness(issue(1, minutes=10))
    record = await harness.create()
    listed = await harness.repository.list_all()
    await harness.service.delete(record.id)

    assert listed
    assert await harness.service.run_all() == (0, 0)
    with pytest.raises(MemoryImportNotFoundError):
        await harness.service.run(record.id)


async def test_creating_an_import_checks_its_connection() -> None:
    harness = Harness()
    harness.connections.refusal = ImportConnectionError("not a team integration of the project")

    with pytest.raises(ImportConnectionError, match="team integration"):
        await harness.create()

    assert await harness.repository.list_all() == []


async def test_an_unsupported_connector_and_an_empty_source_are_refused() -> None:
    harness = Harness()

    with pytest.raises(ValueError, match="not supported"):
        await harness.service.create(
            project_id=PROJECT_ID,
            tenant_id="default",
            owner_id="jozef",
            connector="slack",
            connection_id=CONNECTION,
            tracker_project_id="x",
            compile=True,
        )
    with pytest.raises(ValueError, match="tracker_project_id"):
        await harness.service.create(
            project_id=PROJECT_ID,
            tenant_id="default",
            owner_id="jozef",
            connector="linear",
            connection_id=CONNECTION,
            tracker_project_id="  ",
            compile=True,
        )


async def test_running_a_missing_import_is_an_error() -> None:
    with pytest.raises(MemoryImportNotFoundError):
        await Harness().service.run(uuid4())


def test_the_source_text_is_the_ticket_with_its_comments_and_does_not_depend_on_when() -> None:
    ticket = issue(
        482,
        minutes=5,
        description="Move booking-api to the new region.",
        comments=(
            IssueComment("Ines Moreau", "p99 budget is 300 ms", BASE_TIME),
            IssueComment("", "Agreed", BASE_TIME + timedelta(hours=1)),
        ),
    )

    text = issue_source_content(ticket)

    assert text.startswith("# LIN-482 Ticket 482\n")
    assert "Status: In Progress" in text
    assert "Labels: backend" in text
    assert "URL: https://linear.app/niuu/issue/LIN-482" in text
    assert "Move booking-api to the new region." in text
    assert "### Ines Moreau, 2026-11-10T12:00:00+00:00\n\np99 budget is 300 ms" in text
    assert "### unknown," in text
    assert text == issue_source_content(ticket)
    assert "(no comments)" in issue_source_content(issue(1, minutes=1))


def test_the_compile_prompt_is_the_one_the_contract_names() -> None:
    assert COMPILE_PROMPT == "Compile these sources into the project's memory"
