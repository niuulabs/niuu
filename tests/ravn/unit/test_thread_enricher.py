"""Unit tests for ThreadEnricher."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from niuu.domain.mimir import MimirPage, MimirPageMeta, MimirSourceMeta, ThreadState
from ravn.adapters.triggers.thread_enricher import ThreadEnricher
from ravn.config import ThreadConfig
from ravn.domain.models import LLMResponse, StopReason, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thread_config(
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
    path: str = "technical/test.md",
    title: str = "Test Page",
    summary: str = "A test page",
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
        updated_at=updated_at or datetime(2024, 1, 2, tzinfo=UTC),
        source_ids=source_ids or [],
        thread_state=thread_state,
        is_thread=is_thread,
        produced_by_thread=produced_by_thread,
    )


def _page(
    path: str = "technical/test.md",
    content: str = "# Test\nSome open question here.",
    **kwargs,
) -> MimirPage:
    return MimirPage(meta=_page_meta(path=path, **kwargs), content=content)


def _source_meta(
    source_id: str = "src-1",
    source_type: str = "web",
) -> MimirSourceMeta:
    return MimirSourceMeta(
        source_id=source_id,
        title="Test Source",
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_type=source_type,
    )


def _llm_response(
    is_thread: bool = True,
    confidence: float = 0.9,
    next_action_hint: str | None = "Follow up on the open question.",
) -> LLMResponse:
    content = json.dumps(
        {
            "is_thread": is_thread,
            "confidence": confidence,
            "next_action_hint": next_action_hint,
        }
    )
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=50, output_tokens=20),
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
        config = _thread_config()
    return ThreadEnricher(mimir=mimir, llm=llm, config=config, state_dir=state_dir)


# ---------------------------------------------------------------------------
# Basic properties
# ---------------------------------------------------------------------------


class TestThreadEnricherName:
    def test_name(self) -> None:
        assert _make_enricher().name == "thread_enricher"


# ---------------------------------------------------------------------------
# Disabled behaviour
# ---------------------------------------------------------------------------


class TestThreadEnricherDisabled:
    @pytest.mark.asyncio
    async def test_disabled_exits_without_polling(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(
            mimir=mimir,
            config=_thread_config(enabled=False),
        )
        # Should return immediately — no poll, no sleep
        await enricher.run(AsyncMock())
        mimir.list_pages.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(side_effect=asyncio.CancelledError())
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(mimir=mimir)
        with pytest.raises(asyncio.CancelledError):
            await enricher.run(AsyncMock())


# ---------------------------------------------------------------------------
# Efficiency: early-exit avoids list_sources() round-trip
# ---------------------------------------------------------------------------


class TestEarlyExit:
    @pytest.mark.asyncio
    async def test_no_candidates_skips_list_sources(self) -> None:
        """When all pages are pre-filtered, list_sources() must not be called."""
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[])
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.list_sources.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_already_threads_skips_list_sources(self) -> None:
        """Pages that fail the pre-filter must not trigger a list_sources() call."""
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            return_value=[
                _page_meta(thread_state=ThreadState.open),
                _page_meta(path="threads/x", is_thread=True),
            ]
        )
        mimir.list_sources = AsyncMock(return_value=[])

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.list_sources.assert_not_called()

    @pytest.mark.asyncio
    async def test_eligible_candidate_triggers_list_sources(self) -> None:
        """A page that passes the pre-filter must cause list_sources() to be called."""
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.list_sources.assert_called_once()


# ---------------------------------------------------------------------------
# Eligibility filtering
# ---------------------------------------------------------------------------


class TestEligibilityRules:
    @pytest.mark.asyncio
    async def test_skips_threads_path(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(path="threads/open-question")])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_pages_with_thread_state_set(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(thread_state=ThreadState.open)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_pages_with_is_thread_true(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(is_thread=True)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_pages_with_produced_by_thread(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(produced_by_thread=True)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_tool_output_source_type(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-tool"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-tool", source_type="tool_output")]
        )
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_research_source_type(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-res"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-res", source_type="research")]
        )
        mimir.get_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_research_artifact_written_by_thread_is_not_reclassified(self) -> None:
        """Explicit test: a research artifact from a thread action shape is skipped.

        This verifies the cascade-prevention contract: action shapes set
        produced_by_thread=True on their output; the enricher must never
        re-classify such pages as new threads.
        """
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(
            return_value=[
                _page_meta(
                    path="projects/research-output.md",
                    source_ids=["src-res"],
                    produced_by_thread=True,
                )
            ]
        )
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-res", source_type="research")]
        )
        mimir.get_page = AsyncMock()
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        await enricher._poll_once()

        mimir.get_page.assert_not_called()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_pages_not_updated_since_last_check(self) -> None:
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
    async def test_processes_pages_updated_after_last_check(self) -> None:
        new_time = datetime(2024, 1, 3, tzinfo=UTC)
        last_checked = datetime(2024, 1, 2, tzinfo=UTC)

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(updated_at=new_time)])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        enricher = _make_enricher(mimir=mimir)
        enricher._last_checked_at = last_checked
        await enricher._poll_once()

        mimir.get_page.assert_called_once()


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------


class TestLLMClassification:
    @pytest.mark.asyncio
    async def test_eligible_page_is_classified_and_tagged(self) -> None:
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
        tagged_meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert tagged_meta.thread_state == ThreadState.open
        assert tagged_meta.is_thread is True
        assert tagged_meta.thread_weight is not None
        assert tagged_meta.thread_weight > 0

    @pytest.mark.asyncio
    async def test_non_thread_page_is_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=False, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_page_is_not_tagged(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.5))

        cfg = _thread_config(confidence_threshold=0.7)
        enricher = _make_enricher(mimir=mimir, llm=llm, config=cfg)
        await enricher._poll_once()

        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_fails_the_sweep(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        with pytest.raises(RuntimeError, match="LLM unavailable"):
            await enricher._poll_once()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_invalid_json_fails_the_sweep(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        bad_response = LLMResponse(
            content="not valid json {{",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=bad_response)

        enricher = _make_enricher(mimir=mimir, llm=llm)
        with pytest.raises(ValueError, match="no JSON"):
            await enricher._poll_once()
        mimir.upsert_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_page_called_with_meta_kwarg(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta()])
        mimir.list_sources = AsyncMock(return_value=[])
        mimir.get_page = AsyncMock(return_value=_page())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        mimir.upsert_page.assert_called_once()
        kwargs = mimir.upsert_page.call_args.kwargs
        assert "meta" in kwargs
        assert kwargs["meta"] is not None

    @pytest.mark.asyncio
    async def test_next_action_hint_written_to_meta(self) -> None:
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

    @pytest.mark.asyncio
    async def test_conversation_source_sets_operator_engagement(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-conv"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-conv", source_type="conversation")]
        )
        mimir.get_page = AsyncMock(return_value=_page(source_ids=["src-conv"]))
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert meta.thread_weight_signals["operator_engagement_count"] == 1

    @pytest.mark.asyncio
    async def test_non_conversation_source_zero_operator_engagement(self) -> None:
        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[_page_meta(source_ids=["src-web"])])
        mimir.list_sources = AsyncMock(
            return_value=[_source_meta(source_id="src-web", source_type="web")]
        )
        mimir.get_page = AsyncMock(return_value=_page(source_ids=["src-web"]))
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm)
        await enricher._poll_once()

        meta = mimir.upsert_page.call_args.kwargs["meta"]
        assert meta.thread_weight_signals["operator_engagement_count"] == 0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_load_state_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        enricher = _make_enricher(state_dir=tmp_path)
        assert enricher._load_state() is None

    def test_save_and_load_state_roundtrip(self, tmp_path: Path) -> None:
        enricher = _make_enricher(state_dir=tmp_path)
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        enricher._save_state(ts)
        loaded = enricher._load_state()
        assert loaded is not None
        assert loaded == ts

    def test_load_state_raises_on_a_corrupt_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "thread_enricher_state.json"
        state_file.write_text("not valid json", encoding="utf-8")
        enricher = _make_enricher(state_dir=tmp_path)
        # Starting over silently would re-classify every page.
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
    async def test_run_loads_state_on_start(self, tmp_path: Path) -> None:
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
            config=_thread_config(poll_interval=1),
            state_dir=tmp_path,
        )
        with pytest.raises(asyncio.CancelledError):
            await enricher.run(AsyncMock())

        assert enricher._last_checked_at == saved_ts


# ---------------------------------------------------------------------------
# Poll error handling
# ---------------------------------------------------------------------------


class TestPollErrorHandling:
    @pytest.mark.asyncio
    async def test_page_processing_error_fails_the_poll_without_advancing(
        self, tmp_path: Path
    ) -> None:
        """A page that fails stops the sweep, so the next one retries it instead of skipping it."""
        page_a = _page_meta(path="technical/a.md")
        page_b = _page_meta(path="technical/b.md")

        mimir = AsyncMock()
        mimir.list_pages = AsyncMock(return_value=[page_a, page_b])
        mimir.list_sources = AsyncMock(return_value=[])
        # First call raises; second returns a valid page
        mimir.get_page = AsyncMock(
            side_effect=[
                RuntimeError("transient error"),
                _page(path="technical/b.md"),
            ]
        )
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response(is_thread=True, confidence=0.9))

        enricher = _make_enricher(mimir=mimir, llm=llm, state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="transient error"):
            await enricher._poll_once()

        assert mimir.get_page.call_count == 1
        assert enricher._load_state() is None
