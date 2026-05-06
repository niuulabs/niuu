"""Unit tests for inline fact detection and Mímir page writing (NIU-576).

Covers:
- detect_fact_type() pattern matching
- is_retraction() retraction pattern matching
- detect_and_write() Mímir page path and content correctness
- MemoryPort base on_turn_complete() rolling summary truncation
- MemoryPort.get_rolling_summary() retrieval
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.memory.inline_facts import (
    _build_fact_page,
    _build_retraction_page,
    _slug,
    detect_and_write,
    detect_fact_type,
    is_retraction,
)
from ravn.ports.memory import _ROLLING_SUMMARY_MAX_CHARS, MemoryPort

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mimir() -> MagicMock:
    """Return a mock MimirPort whose upsert_page() is an AsyncMock."""
    m = MagicMock()
    m.upsert_page = AsyncMock()
    return m


# ---------------------------------------------------------------------------
# detect_fact_type()
# ---------------------------------------------------------------------------


class TestDetectFactType:
    def test_preference_i_prefer(self) -> None:
        assert detect_fact_type("I prefer early returns over nested conditionals") == "preference"

    def test_preference_i_like(self) -> None:
        assert detect_fact_type("I like using type annotations everywhere") == "preference"

    def test_preference_i_dont_like(self) -> None:
        assert detect_fact_type("I don't like deeply nested code") == "preference"

    def test_preference_i_hate(self) -> None:
        assert detect_fact_type("I hate magic numbers in business logic") == "preference"

    def test_preference_i_love(self) -> None:
        assert detect_fact_type("I love clean architecture") == "preference"

    def test_decision_we_decided(self) -> None:
        assert detect_fact_type("We decided to use RabbitMQ as the transport") == "decision"

    def test_decision_lets_go_with(self) -> None:
        assert detect_fact_type("Let's go with SQLite for local dev") == "decision"

    def test_decision_were_going_with(self) -> None:
        assert detect_fact_type("We're going with asyncpg for DB access") == "decision"

    def test_decision_we_chose(self) -> None:
        assert detect_fact_type("We chose nng for inter-region messaging") == "decision"

    def test_decision_we_picked(self) -> None:
        assert detect_fact_type("We picked Haiku for the reflection model") == "decision"

    def test_directive_remember_that(self) -> None:
        assert detect_fact_type("Remember that all migrations need a down file") == "directive"

    def test_directive_note_that(self) -> None:
        text = "Note that the config uses double underscores for nesting"
        assert detect_fact_type(text) == "directive"

    def test_directive_dont_forget(self) -> None:
        assert detect_fact_type("Don't forget to update the Helm configmap too") == "directive"

    def test_no_match_returns_none(self) -> None:
        assert detect_fact_type("How does asyncpg handle connection pooling?") is None

    def test_case_insensitive(self) -> None:
        assert detect_fact_type("WE DECIDED to use Postgres") == "decision"

    def test_decision_wins_over_preference(self) -> None:
        # decision pattern takes priority in our match order
        text = "We decided I prefer the new approach"
        assert detect_fact_type(text) == "decision"


# ---------------------------------------------------------------------------
# is_retraction()
# ---------------------------------------------------------------------------


class TestIsRetraction:
    def test_forget_that(self) -> None:
        assert is_retraction("forget that, I changed my mind") is True

    def test_actually_no(self) -> None:
        assert is_retraction("Actually no, let's stick with SQLite") is True

    def test_ignore_what_i_said(self) -> None:
        assert is_retraction("Ignore what I said about using Redis") is True

    def test_scratch_that(self) -> None:
        assert is_retraction("Scratch that, the old approach is fine") is True

    def test_no_retraction(self) -> None:
        assert is_retraction("I prefer early returns") is False

    def test_case_insensitive(self) -> None:
        assert is_retraction("FORGET THAT entirely") is True


# ---------------------------------------------------------------------------
# _slug()
# ---------------------------------------------------------------------------


class TestSlug:
    def test_basic(self) -> None:
        slug = _slug("I prefer early returns in functions")
        assert slug == "i-prefer-early-returns-in-functions"

    def test_truncates_long_text(self) -> None:
        long = " ".join(["word"] * 20)
        slug = _slug(long)
        assert len(slug) <= 48

    def test_word_limit(self) -> None:
        text = "one two three four five six seven eight nine ten"
        slug = _slug(text)
        # Only first 8 words should appear
        assert "nine" not in slug
        assert "ten" not in slug

    def test_special_chars_replaced(self) -> None:
        slug = _slug("don't forget! this is important.")
        assert "!" not in slug
        assert "'" not in slug


# ---------------------------------------------------------------------------
# _build_fact_page() / _build_retraction_page()
# ---------------------------------------------------------------------------


class TestPageBuilders:
    def test_fact_page_contains_type(self) -> None:
        now = datetime(2026, 4, 12, 0, 0, 0, tzinfo=UTC)
        page = _build_fact_page("preference", "I prefer early returns", now)
        assert "type: preference" in page
        assert "# I prefer early returns" in page
        assert "2026-04-12" in page

    def test_fact_page_valid_from_iso(self) -> None:
        now = datetime(2026, 4, 12, 9, 30, 0, tzinfo=UTC)
        page = _build_fact_page("decision", "We decided to use nng", now)
        assert "valid_from: 2026-04-12T09:30:00Z" in page

    def test_retraction_page_contains_valid_until(self) -> None:
        now = datetime(2026, 4, 12, 0, 0, 0, tzinfo=UTC)
        page = _build_retraction_page("I prefer Redis", retracted_at=now)
        assert "type: retracted" in page
        assert "valid_until: 2026-04-12T00:00:00Z" in page
        assert "~~I prefer Redis~~" in page


# ---------------------------------------------------------------------------
# detect_and_write() integration
# ---------------------------------------------------------------------------


class TestDetectAndWrite:
    @pytest.mark.asyncio
    async def test_preference_writes_to_mimir(self) -> None:
        mimir = _make_mimir()
        await detect_and_write("I prefer early returns", mimir)

        mimir.upsert_page.assert_awaited_once()
        path, content = mimir.upsert_page.call_args[0]
        assert path.startswith("memory/preferences/")
        assert path.endswith(".md")
        assert "type: preference" in content

    @pytest.mark.asyncio
    async def test_decision_writes_to_mimir(self) -> None:
        mimir = _make_mimir()
        await detect_and_write("We decided to use SQLite", mimir)

        path, content = mimir.upsert_page.call_args[0]
        assert path.startswith("memory/decisions/")
        assert "type: decision" in content

    @pytest.mark.asyncio
    async def test_directive_writes_to_mimir(self) -> None:
        mimir = _make_mimir()
        await detect_and_write("Remember that we need a down migration", mimir)

        path, content = mimir.upsert_page.call_args[0]
        assert path.startswith("memory/directives/")
        assert "type: directive" in content

    @pytest.mark.asyncio
    async def test_retraction_writes_to_retractions(self) -> None:
        mimir = _make_mimir()
        await detect_and_write("Forget that, I changed my mind", mimir)

        path, content = mimir.upsert_page.call_args[0]
        assert path.startswith("memory/retractions/")
        assert "type: retracted" in content

    @pytest.mark.asyncio
    async def test_no_match_does_not_write(self) -> None:
        mimir = _make_mimir()
        await detect_and_write("How does the connection pool work?", mimir)

        mimir.upsert_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mimir_error_is_swallowed(self) -> None:
        mimir = _make_mimir()
        mimir.upsert_page = AsyncMock(side_effect=RuntimeError("storage unavailable"))

        # Must not raise
        await detect_and_write("I prefer early returns", mimir)


# ---------------------------------------------------------------------------
# MemoryPort base rolling summary
# ---------------------------------------------------------------------------


class _MinimalMemory(MemoryPort):
    """Minimal concrete MemoryPort for testing the base-class rolling summary."""

    async def record_episode(self, episode):  # type: ignore[override]
        pass

    async def query_episodes(self, query, *, limit=5, min_relevance=0.3):  # type: ignore[override]
        return []

    async def prefetch(self, context):  # type: ignore[override]
        return ""

    async def search_sessions(self, query, *, limit=3):  # type: ignore[override]
        return []

    def inject_shared_context(self, context):  # type: ignore[override]
        pass

    def get_shared_context(self):  # type: ignore[override]
        return None


class TestRollingSessionSummary:
    @pytest.mark.asyncio
    async def test_summary_starts_empty(self) -> None:
        mem = _MinimalMemory()
        assert mem.get_rolling_summary("sess-abc") == ""

    @pytest.mark.asyncio
    async def test_single_turn_stored(self) -> None:
        mem = _MinimalMemory()
        await mem.on_turn_complete("sess-1", "hello", "hi there")

        summary = mem.get_rolling_summary("sess-1")
        assert "hello" in summary
        assert "hi there" in summary

    @pytest.mark.asyncio
    async def test_multiple_turns_accumulate(self) -> None:
        mem = _MinimalMemory()
        await mem.on_turn_complete("sess-1", "first question", "first answer")
        await mem.on_turn_complete("sess-1", "second question", "second answer")

        summary = mem.get_rolling_summary("sess-1")
        assert "first question" in summary
        assert "second question" in summary

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(self) -> None:
        mem = _MinimalMemory()
        await mem.on_turn_complete("sess-A", "question A", "answer A")
        await mem.on_turn_complete("sess-B", "question B", "answer B")

        assert "question A" in mem.get_rolling_summary("sess-A")
        assert "question A" not in mem.get_rolling_summary("sess-B")
        assert "question B" in mem.get_rolling_summary("sess-B")

    @pytest.mark.asyncio
    async def test_summary_truncated_at_max(self) -> None:
        mem = _MinimalMemory()
        # Build up > _ROLLING_SUMMARY_MAX_CHARS worth of content
        long_input = "x" * 300
        long_response = "y" * 500
        for _ in range(5):
            await mem.on_turn_complete("sess-1", long_input, long_response)

        summary = mem.get_rolling_summary("sess-1")
        assert len(summary) <= _ROLLING_SUMMARY_MAX_CHARS
