"""Unit tests for ThreadEnricher trigger adapter (NIU-564).

Uses mocked LLMPort and MimirPort — no real filesystem or network calls.

Covers:
- Page with open question → is_thread=True → mimir.upsert_page called with thread_state=open
- Page already classified → skipped (no re-classification)
- Page with fact/reference → is_thread=False → mimir.upsert_page not called
- LLM fails → enricher logs, continues, no crash
- last_checked_at persisted and respected on restart
- thread.enabled=False → no polling
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from niuu.domain.mimir import MimirPage, MimirPageMeta, MimirSourceMeta, ThreadState
from ravn.adapters.triggers.thread_enricher import ThreadEnricher
from ravn.config import ThreadConfig
from ravn.domain.exceptions import OutputTruncatedError
from ravn.domain.models import LLMResponse, StopReason, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    enabled: bool = True,
    confidence_threshold: float = 0.7,
    poll_interval: int = 1,
) -> ThreadConfig:
    return ThreadConfig(
        enabled=enabled,
        enricher_poll_interval_seconds=poll_interval,
        confidence_threshold=confidence_threshold,
    )


def _page_meta(
    path: str = "technical/open-question.md",
    title: str = "Open Question",
    summary: str = "An open question about retrieval",
    updated_at: datetime | None = None,
    source_ids: list[str] | None = None,
    thread_state: ThreadState | None = None,
    is_thread: bool = False,
    produced_by_thread: bool = False,
) -> MimirPageMeta:
    return MimirPageMeta(
        path=path,
        title=title,
        summary=summary,
        category="technical",
        updated_at=updated_at or datetime(2024, 3, 1, tzinfo=UTC),
        source_ids=source_ids or [],
        thread_state=thread_state,
        is_thread=is_thread,
        produced_by_thread=produced_by_thread,
    )


def _page(
    path: str = "technical/open-question.md",
    content: str = "# Open Question\nWe need to investigate this further.",
    **kwargs,
) -> MimirPage:
    return MimirPage(meta=_page_meta(path=path, **kwargs), content=content)


def _source_meta(source_id: str = "src-1", source_type: str = "web") -> MimirSourceMeta:
    return MimirSourceMeta(
        source_id=source_id,
        title="A Source",
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_type=source_type,
    )


def _llm_response(
    is_thread: bool = True,
    confidence: float = 0.9,
    next_action_hint: str | None = "Investigate the open question.",
) -> LLMResponse:
    payload = json.dumps(
        {"is_thread": is_thread, "confidence": confidence, "next_action_hint": next_action_hint}
    )
    return LLMResponse(
        content=payload,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=40, output_tokens=20),
    )


def _make_enricher(
    mimir: object | None = None,
    llm: object | None = None,
    config: ThreadConfig | None = None,
    state_dir: Path | None = None,
) -> ThreadEnricher:
    if mimir is None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])
        mimir.list_sources = AsyncMock(return_value=[])
    if llm is None:
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response())
    if config is None:
        config = _config()
    return ThreadEnricher(mimir=mimir, llm=llm, config=config, state_dir=state_dir)


# ---------------------------------------------------------------------------
# thread.enabled=False → no polling
# ---------------------------------------------------------------------------


class TestDisabledEnricher:
    @pytest.mark.asyncio
    async def test_disabled_exits_immediately(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(mimir=mimir, config=_config(enabled=False))
        await enricher.run(AsyncMock())

        mimir.list_pages.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_does_not_call_upsert(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir, config=_config(enabled=False))
        await enricher.run(AsyncMock())

        mimir.upsert_page.assert_not_called()


# ---------------------------------------------------------------------------
# Open question page → tagged as thread
# ---------------------------------------------------------------------------


class TestOpenQuestionPage:
    @pytest.mark.asyncio
    async def test_open_question_tagged_as_thread(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.95))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        mimir.upsert_page.assert_called_once()
        meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert meta.thread_state == ThreadState.open
        assert meta.is_thread is True

    @pytest.mark.asyncio
    async def test_open_question_has_positive_weight(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert meta.thread_weight is not None
        assert meta.thread_weight > 0.0

    @pytest.mark.asyncio
    async def test_next_action_hint_stored_in_meta(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        hint = "Review the outstanding items in the backlog."
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(is_thread=True, confidence=0.9, next_action_hint=hint)
        )

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert meta.thread_next_action_hint == hint


# ---------------------------------------------------------------------------
# Already-classified page → skipped
# ---------------------------------------------------------------------------


class TestAlreadyClassifiedPage:
    @pytest.mark.asyncio
    async def test_page_with_thread_state_skipped(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(thread_state=ThreadState.open)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_page_with_is_thread_true_skipped(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(is_thread=True)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_threads_path_skipped(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(path="threads/existing-thread")])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_produced_by_thread_skipped(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(produced_by_thread=True)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()


# ---------------------------------------------------------------------------
# Fact/reference page → is_thread=False → not tagged
# ---------------------------------------------------------------------------


class TestFactOrReferencePage:
    @pytest.mark.asyncio
    async def test_fact_page_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(
            return_value=_page(content="# Auth Overview\nThis page describes how auth works.")
        )
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=False, confidence=0.95))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.4))

        enricher = _make_enricher(mimir=mimir, llm=llm, config=_config(confidence_threshold=0.7))
        await enricher._poll_once()

        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_output_source_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-tool"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-tool", source_type="tool_output")]
        )
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_research_source_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-res"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-res", source_type="research")]
        )
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()


# ---------------------------------------------------------------------------
# LLM failures → enricher logs, continues, no crash
# ---------------------------------------------------------------------------


class TestLLMFailures:
    """A failed classification fails the sweep, which then retries the same pages.

    These were "does not crash" tests: the failure was logged, the watermark
    advanced past the page, and the page was never classified.
    """

    @pytest.mark.asyncio
    async def test_llm_exception_fails_the_sweep_without_advancing(self, tmp_path: Path) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        enricher = _make_enricher(mimir=mimir, llm=llm, state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="LLM unavailable"):
            await enricher._poll_once()
        mimir.upsert_page.assert_not_called()
        assert enricher._load_state() is None

    @pytest.mark.asyncio
    async def test_llm_invalid_json_raises_naming_the_model(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        bad = LLMResponse(
            content="definitely not json {{",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=bad)

        enricher = _make_enricher(mimir=mimir, llm=llm)
        with pytest.raises(ValueError, match="enricher_llm_alias"):
            await enricher._poll_once()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_object_json_raises(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="[1, 2]",
                tool_calls=[],
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )
        )

        enricher = _make_enricher(mimir=mimir, llm=llm)
        with pytest.raises(ValueError, match="not an object"):
            await enricher._poll_once()

    @pytest.mark.asyncio
    async def test_truncated_classification_raises_and_sends_no_cap(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())

        cut = LLMResponse(
            content='{"is_thread": tr',
            tool_calls=[],
            stop_reason=StopReason.MAX_TOKENS,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=cut)

        enricher = _make_enricher(mimir=mimir, llm=llm)
        with pytest.raises(OutputTruncatedError, match="thread.enricher"):
            await enricher._poll_once()
        assert llm.generate.call_args.kwargs["max_tokens"] is None

    @pytest.mark.asyncio
    async def test_a_page_error_stops_the_sweep(self, tmp_path: Path) -> None:
        page_a = _page_meta(path="technical/a.md")
        page_b = _page_meta(path="technical/b.md")

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[page_a, page_b])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(
            side_effect=[RuntimeError("transient error"), _page(path="technical/b.md")]
        )
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm, state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="transient error"):
            await enricher._poll_once()
        # Not advanced, so the next sweep looks at page a again.
        assert enricher._load_state() is None

    @pytest.mark.asyncio
    async def test_run_logs_a_failed_sweep_as_an_error_and_keeps_polling(
        self, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            side_effect=[RuntimeError("mimir down"), asyncio.CancelledError()]
        )

        enricher = _make_enricher(mimir=mimir, state_dir=tmp_path)
        enricher._config.enricher_poll_interval_seconds = 0
        with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
            await enricher.run(AsyncMock())
        assert any(
            r.levelno == logging.ERROR and "sweep failed" in r.getMessage() for r in caplog.records
        )


# ---------------------------------------------------------------------------
# last_checked_at persisted and respected on restart
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_load_state_returns_none_when_missing(self, tmp_path: Path) -> None:
        enricher = _make_enricher(state_dir=tmp_path)
        assert enricher._load_state() is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        enricher = _make_enricher(state_dir=tmp_path)
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        enricher._save_state(ts)
        loaded = enricher._load_state()
        assert loaded == ts

    def test_load_raises_on_a_corrupt_state_file(self, tmp_path: Path) -> None:
        # Starting over would silently re-classify every page.
        state_file = tmp_path / "thread_enricher_state.json"
        state_file.write_text("not valid json", encoding="utf-8")
        enricher = _make_enricher(state_dir=tmp_path)
        with pytest.raises(json.JSONDecodeError):
            enricher._load_state()

    @pytest.mark.asyncio
    async def test_poll_once_persists_last_checked_at(self, tmp_path: Path) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(mimir=mimir, state_dir=tmp_path)
        await enricher._poll_once()

        state_file = tmp_path / "thread_enricher_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "last_checked_at" in data

    @pytest.mark.asyncio
    async def test_pages_before_last_checked_at_skipped(self) -> None:
        old_time = datetime(2024, 1, 1, tzinfo=UTC)
        last_checked = datetime(2024, 1, 2, tzinfo=UTC)

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(updated_at=old_time)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        enricher._last_checked_at = last_checked
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_pages_after_last_checked_at_processed(self) -> None:
        new_time = datetime(2024, 1, 3, tzinfo=UTC)
        last_checked = datetime(2024, 1, 2, tzinfo=UTC)

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(updated_at=new_time)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page(updated_at=new_time))
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        enricher._last_checked_at = last_checked
        await enricher._poll_once()

        mimir.get_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_loads_state_from_file_before_first_poll(self, tmp_path: Path) -> None:
        saved_ts = datetime(2024, 1, 1, tzinfo=UTC)
        state_file = tmp_path / "thread_enricher_state.json"
        state_file.write_text(
            json.dumps({"last_checked_at": saved_ts.isoformat()}), encoding="utf-8"
        )

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(side_effect=asyncio.CancelledError())
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(
            mimir=mimir,
            config=_config(poll_interval=1),
            state_dir=tmp_path,
        )
        with pytest.raises(asyncio.CancelledError):
            await enricher.run(AsyncMock())

        assert enricher._last_checked_at == saved_ts
