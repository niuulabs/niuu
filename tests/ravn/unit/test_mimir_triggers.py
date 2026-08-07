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


def _full_source(source_id: str = "src-1", content: str = "Some content here.") -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title="Test Source",
        content=content,
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
        max_content_chars: int = 120_000,
    ) -> MimirSourceTrigger:
        if mimir is None:
            mimir = AsyncMock()
            mimir.list_sources = AsyncMock(return_value=[])
        cfg = MimirSourceTriggerConfig(
            poll_interval_seconds=poll_interval,
            retry_after_seconds=retry_after,
            persona=persona,
            max_content_chars=max_content_chars,
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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_poll_once_enqueues_task_for_source(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1
        assert "Test Source" in enqueued[0].title
        assert enqueued[0].persona == "mimir-curator"

    @pytest.mark.asyncio
    async def test_poll_once_skips_recently_enqueued(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta(source_id="src-1")])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir, retry_after=9999)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1  # second poll skipped

    @pytest.mark.asyncio
    async def test_poll_once_includes_source_content(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        context = enqueued[0].initiative_context
        assert "Some content here." in context

    @pytest.mark.asyncio
    async def test_poll_once_truncates_large_source_content(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source(content="abcdef"))
        trigger = self._make_trigger(mimir=mimir, max_content_chars=3)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        context = enqueued[0].initiative_context
        assert "## Source content\n\nabc" in context
        assert "def" not in context
        assert "Source content truncated" in context

    @pytest.mark.asyncio
    async def test_poll_once_does_not_embed_literal_source_footer_marker(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta(source_id="src-1")])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source(source_id="src-1"))
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        assert "<!-- sources: src-1 -->" not in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_poll_once_handles_missing_source_content(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=None)
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)
        assert len(enqueued) == 1
        assert "unavailable" in enqueued[0].initiative_context.lower()

    @pytest.mark.asyncio
    async def test_poll_once_includes_mount_tag_when_present(self) -> None:
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta(mount_name="gimle-wiki")])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

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

    # -- back-pressure ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_poll_once_stops_reading_once_the_queue_is_full(self) -> None:
        """A refused task means every later one is refused too.

        The shared mount's warden sat on a 138-source backlog and fetched every
        source body — up to 5.5 MB each — before offering the task to a drive
        loop that was already at its 50-task cap, so most of those reads were
        thrown away and Mímir paid for all of them every ten minutes.
        """
        metas = [_source_meta(source_id=f"src-{i}") for i in range(10)]
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=metas)
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)

        accepted: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            if len(accepted) >= 2:
                return False
            accepted.append(task)
            return True

        await trigger._poll_once(_enqueue)

        assert len(accepted) == 2
        # Two accepted plus the one that was refused — not all ten.
        assert mimir.read_source_excerpt.await_count == 3

    @pytest.mark.asyncio
    async def test_a_refused_source_is_retried_on_the_next_sweep(self) -> None:
        """Suppressing a task the queue refused would strand it for retry_after."""
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)

        async def _refuse(task: AgentTask) -> bool:
            return False

        await trigger._poll_once(_refuse)

        accepted: list[AgentTask] = []

        async def _accept(task: AgentTask) -> bool:
            accepted.append(task)
            return True

        await trigger._poll_once(_accept)

        assert len(accepted) == 1

    @pytest.mark.asyncio
    async def test_poll_once_drains_the_backlog_oldest_first(self) -> None:
        newest = _source_meta(source_id="src-new")
        newest.ingested_at = datetime(2026, 8, 1, tzinfo=UTC)
        oldest = _source_meta(source_id="src-old")
        oldest.ingested_at = datetime(2026, 6, 1, tzinfo=UTC)
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[newest, oldest])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)

        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)

        assert ["src-old", "src-new"] == [t.task_id.rsplit("_", 1)[-1] for t in enqueued]

    @pytest.mark.asyncio
    async def test_poll_once_skips_operational_sources(self) -> None:
        """Exhaust can never be cited by a page, so it can never stop being swept.

        The shared mount holds 61 such sources — dream-cycle markers, lint
        reports, proof readbacks — ingested before the gate existed. Without
        this skip they are re-swept every poll, forever.
        """
        knowledge = _source_meta(source_id="src-real", title="A datasheet")
        exhaust = _source_meta(source_id="src-log", title="Dream cycle 2026-06-18T08:15")
        exhaust.source_type = "tool_output"
        probe = _source_meta(source_id="src-probe", title="Mimir health small ingest probe")
        probe.source_type = "diagnostic"

        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[exhaust, probe, knowledge])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir)

        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._poll_once(_enqueue)

        assert [t.title for t in enqueued] == ["Synthesise Mímir source: A datasheet"]
        # Skipped before the read — exhaust costs nothing at all now.
        mimir.read_source_excerpt.assert_awaited_once_with("src-real", 120_000)

    @pytest.mark.asyncio
    async def test_poll_once_asks_for_a_bounded_excerpt(self) -> None:
        """The context truncates anyway — do not ship the megabytes first."""
        mimir = AsyncMock()
        mimir.list_sources = AsyncMock(return_value=[_source_meta()])
        mimir.read_source_excerpt = AsyncMock(return_value=_full_source())
        trigger = self._make_trigger(mimir=mimir, max_content_chars=5_000)

        async def _enqueue(task: AgentTask) -> bool:
            return True

        await trigger._poll_once(_enqueue)

        mimir.read_source_excerpt.assert_awaited_once_with("src-1", 5_000)


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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

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

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        await trigger._check_once(_enqueue)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        usage = AsyncMock()
        usage.top_pages = AsyncMock(side_effect=asyncio.CancelledError())
        trigger = self._make_trigger(usage=usage, schedule_hours=1)
        with pytest.raises(asyncio.CancelledError):
            await trigger.run(AsyncMock())
