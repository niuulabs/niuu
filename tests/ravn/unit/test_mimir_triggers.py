"""Unit tests for MimirSourceTrigger and MimirStalenessTrigger."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.domain.mimir import LintIssue, MimirLintReport
from niuu.ports.mimir import MimirPageMeta, MimirSource, MimirSourceMeta
from ravn.adapters.triggers.mimir_source import MimirSourceTrigger
from ravn.adapters.triggers.mimir_staleness import MimirStalenessTrigger
from ravn.config import MimirSourceTriggerConfig, MimirStalenessTriggerConfig
from ravn.domain.models import AgentTask


def _source_meta(
    source_id: str = "src-1",
    title: str = "Test Source",
    mount_name: str | None = None,
) -> MimirSourceMeta:
    return MimirSourceMeta(
        source_id=source_id,
        title=title,
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_type="web",
        mount_name=mount_name,
    )


def _full_source(source_id: str = "src-1") -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title="Test Source",
        content="Some content here.",
        source_type="web",
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        content_hash="abc123",
    )


def _page_meta(
    path: str = "wiki/a.md",
    source_ids: list[str] | None = None,
) -> MimirPageMeta:
    return MimirPageMeta(
        path=path,
        title="A Page",
        summary="Summary",
        category="technical",
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_ids=source_ids or [],
    )


def _lint_report(stale: list[str] | None = None) -> MimirLintReport:
    issues = [
        LintIssue(id="L08", severity="info", message="stale", page_path=p) for p in (stale or [])
    ]
    return MimirLintReport(issues=issues, pages_checked=1)


# ---------------------------------------------------------------------------
# MimirSourceTrigger
# ---------------------------------------------------------------------------


class TestMimirSourceTrigger:
    def _make_trigger(
        self,
        mimir: object | None = None,
        poll_interval: int = 60,
        retry_after: int = 600,
        persona: str = "mimir-curator",
    ) -> MimirSourceTrigger:
        if mimir is None:
            mimir = AsyncMock()
            mimir.list_sources = AsyncMock(return_value=[])
        cfg = MimirSourceTriggerConfig(
            poll_interval_seconds=poll_interval,
            retry_after_seconds=retry_after,
            persona=persona,
        )
        return MimirSourceTrigger(mimir, cfg)

    def test_name(self) -> None:
        assert self._make_trigger().name == "mimir_source"

    @pytest.mark.asyncio
    async def test_poll_once_no_sources_enqueues_nothing(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[])
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._poll_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_poll_once_enqueues_task_for_source(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1
        assert "Test Source" in enqueued[0].title
        assert enqueued[0].persona == "mimir-curator"

    @pytest.mark.asyncio
    async def test_poll_once_skips_recently_enqueued(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta(source_id="src-1")])
        mimir.read_source = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir, retry_after=9999)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._poll_once(_enqueue)
        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1  # second poll skipped

    @pytest.mark.asyncio
    async def test_poll_once_includes_source_content(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._poll_once(_enqueue)
        context = enqueued[0].initiative_context
        assert "Some content here." in context

    @pytest.mark.asyncio
    async def test_poll_once_handles_missing_source_content(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source = AsyncMock(return_value=None)
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1
        assert "unavailable" in enqueued[0].initiative_context.lower()

    @pytest.mark.asyncio
    async def test_poll_once_includes_mount_tag_when_present(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta(mount_name="gimle-wiki")])
        mimir.read_source = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        # Just confirm it doesn't crash with mount_name set
        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1

    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(side_effect=asyncio.CancelledError())
        trigger = self._make_trigger(mimir=mimir, poll_interval=1)
        with pytest.raises(asyncio.CancelledError):
            await trigger.run(AsyncMock())


# ---------------------------------------------------------------------------
# MimirStalenessTrigger
# ---------------------------------------------------------------------------


class TestMimirStalenessTrigger:
    def _make_trigger(
        self,
        mimir: object | None = None,
        usage: object | None = None,
        schedule_hours: int = 6,
        top_n: int = 20,
    ) -> MimirStalenessTrigger:
        if mimir is None:
            mimir = AsyncMock()
        if usage is None:
            usage = AsyncMock()
            usage.top_pages = AsyncMock(return_value=[])
        cfg = MimirStalenessTriggerConfig(
            schedule_hours=schedule_hours,
            top_n=top_n,
            persona="mimir-curator",
        )
        return MimirStalenessTrigger(mimir, usage, cfg)

    def test_name(self) -> None:
        assert self._make_trigger().name == "mimir_staleness"

    @pytest.mark.asyncio
    async def test_check_once_no_usage_data_skips(self) -> None:
        usage = AsyncMock()
        usage.top_pages = AsyncMock(return_value=[])
        trigger = self._make_trigger(usage=usage)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._check_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_check_once_fresh_page_not_enqueued(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            return_value=[_page_meta(path="wiki/a.md", source_ids=["src-1"])]
        )
        mimir.lint = AsyncMock(return_value=_lint_report(stale=[]))  # not stale
        usage = AsyncMock()
        usage.top_pages = AsyncMock(return_value=[("wiki/a.md", 5)])
        trigger = self._make_trigger(mimir=mimir, usage=usage)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._check_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_check_once_stale_page_enqueued(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            return_value=[_page_meta(path="wiki/a.md", source_ids=["src-1"])]
        )
        mimir.lint = AsyncMock(return_value=_lint_report(stale=["wiki/a.md"]))
        usage = AsyncMock()
        usage.top_pages = AsyncMock(return_value=[("wiki/a.md", 5)])
        trigger = self._make_trigger(mimir=mimir, usage=usage)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._check_once(_enqueue)
        assert len(enqueued) == 1
        assert "wiki/a.md" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_check_once_deduplicates_same_page(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            return_value=[_page_meta(path="wiki/a.md", source_ids=["src-1"])]
        )
        mimir.lint = AsyncMock(return_value=_lint_report(stale=["wiki/a.md"]))
        usage = AsyncMock()
        usage.top_pages = AsyncMock(return_value=[("wiki/a.md", 5)])
        trigger = self._make_trigger(mimir=mimir, usage=usage)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._check_once(_enqueue)
        await trigger._check_once(_enqueue)
        assert len(enqueued) == 1  # deduplicated

    @pytest.mark.asyncio
    async def test_check_once_skips_unknown_path(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])  # no pages registered
        usage = AsyncMock()
        usage.top_pages = AsyncMock(return_value=[("wiki/ghost.md", 10)])
        trigger = self._make_trigger(mimir=mimir, usage=usage)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        await trigger._check_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        usage = AsyncMock()
        usage.top_pages = AsyncMock(side_effect=asyncio.CancelledError())
        trigger = self._make_trigger(usage=usage, schedule_hours=1)
        with pytest.raises(asyncio.CancelledError):
            await trigger.run(AsyncMock())
