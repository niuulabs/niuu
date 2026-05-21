"""Tests for the reusable session transcript utility."""

from __future__ import annotations

import pytest

from ting.domain.services.session_transcript import (
    _format_transcript,
    attach_session_transcript,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubVolundrAdapter:
    """Minimal Volundr adapter that records calls."""

    def __init__(self, conversation: dict | None = None) -> None:
        self._conversation = conversation or {"turns": []}
        self.get_conversation_calls: list[str] = []

    async def get_conversation(self, session_id: str) -> dict:
        self.get_conversation_calls.append(session_id)
        return self._conversation


class StubVolundrFactory:
    """Factory returning a pre-configured adapter list."""

    def __init__(self, adapters: list | None = None) -> None:
        self._adapters = adapters if adapters is not None else []

    async def for_owner(self, owner_id: str) -> list:
        return self._adapters


class StubTracker:
    """Tracker that records attach_issue_document calls."""

    def __init__(self) -> None:
        self.attached: list[tuple[str, str, str]] = []

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        self.attached.append((issue_id, title, content))
        return "doc-1"


class ErrorVolundrAdapter:
    """Adapter that raises on get_conversation."""

    async def get_conversation(self, session_id: str) -> dict:
        raise RuntimeError("connection refused")


class ErrorTracker:
    """Tracker that raises on attach."""

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        raise RuntimeError("tracker error")


# ---------------------------------------------------------------------------
# _format_transcript
# ---------------------------------------------------------------------------


class TestFormatTranscript:
    def test_empty_turns(self) -> None:
        title, body = _format_transcript([], "Review Transcript", "my-run")
        assert title == "Review Transcript — my-run"
        assert body.startswith("# Review Transcript")

    def test_single_turn(self) -> None:
        turns = [{"role": "assistant", "content": "Hello"}]
        title, body = _format_transcript(turns, "Working Session Transcript", "run-2")
        assert title == "Working Session Transcript — run-2"
        assert "### Assistant" in body
        assert "Hello" in body
        assert "---" in body

    def test_multiple_turns(self) -> None:
        turns = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "On it"},
            {"role": "user", "content": "Thanks"},
        ]
        title, body = _format_transcript(turns, "Review Transcript", "r")
        assert title == "Review Transcript — r"
        assert body.count("### User") == 2
        assert body.count("### Assistant") == 1

    def test_missing_role_defaults_to_unknown(self) -> None:
        turns = [{"content": "no role"}]
        _, body = _format_transcript(turns, "T", "r")
        assert "### Unknown" in body

    def test_missing_content_defaults_to_empty(self) -> None:
        turns = [{"role": "user"}]
        _, body = _format_transcript(turns, "T", "r")
        assert "### User" in body


# ---------------------------------------------------------------------------
# attach_session_transcript
# ---------------------------------------------------------------------------


class TestAttachSessionTranscript:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        conversation = {
            "turns": [
                {"role": "assistant", "content": "Reviewing code…"},
                {"role": "user", "content": "Looks good"},
            ]
        }
        adapter = StubVolundrAdapter(conversation)
        factory = StubVolundrFactory([adapter])
        tracker = StubTracker()

        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-1",
            owner_id="owner-abc",
            session_id="sess-123",
            title_prefix="Review Transcript",
            run_name="my-run",
        )

        assert adapter.get_conversation_calls == ["sess-123"]
        assert len(tracker.attached) == 1
        issue_id, title, content = tracker.attached[0]
        assert issue_id == "ISSUE-1"
        assert title == "Review Transcript — my-run"
        assert "### Assistant" in content
        assert "Reviewing code…" in content

    @pytest.mark.asyncio
    async def test_no_adapters_returns_early(self) -> None:
        factory = StubVolundrFactory([])
        tracker = StubTracker()

        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-1",
            owner_id="owner-abc",
            session_id="sess-123",
            title_prefix="Review Transcript",
            run_name="my-run",
        )

        assert len(tracker.attached) == 0

    @pytest.mark.asyncio
    async def test_empty_conversation(self) -> None:
        adapter = StubVolundrAdapter({"turns": []})
        factory = StubVolundrFactory([adapter])
        tracker = StubTracker()

        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-1",
            owner_id="owner-abc",
            session_id="sess-1",
            title_prefix="Working Session Transcript",
            run_name="run-x",
        )

        assert len(tracker.attached) == 1
        _, title, content = tracker.attached[0]
        assert title == "Working Session Transcript — run-x"
        assert "# Working Session Transcript" in content

    @pytest.mark.asyncio
    async def test_volundr_error_swallowed(self) -> None:
        factory = StubVolundrFactory([ErrorVolundrAdapter()])
        tracker = StubTracker()

        # Should not raise
        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-1",
            owner_id="owner-abc",
            session_id="sess-1",
            title_prefix="T",
            run_name="r",
        )

        assert len(tracker.attached) == 0

    @pytest.mark.asyncio
    async def test_tracker_error_swallowed(self) -> None:
        adapter = StubVolundrAdapter({"turns": [{"role": "user", "content": "hi"}]})
        factory = StubVolundrFactory([adapter])
        tracker = ErrorTracker()

        # Should not raise
        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-1",
            owner_id="owner-abc",
            session_id="sess-1",
            title_prefix="T",
            run_name="r",
        )

    @pytest.mark.asyncio
    async def test_working_session_prefix(self) -> None:
        conversation = {"turns": [{"role": "assistant", "content": "Done"}]}
        adapter = StubVolundrAdapter(conversation)
        factory = StubVolundrFactory([adapter])
        tracker = StubTracker()

        await attach_session_transcript(
            volundr_factory=factory,
            tracker=tracker,
            tracker_id="ISSUE-2",
            owner_id="owner-xyz",
            session_id="sess-456",
            title_prefix="Working Session Transcript",
            run_name="run-y",
        )

        _, title, content = tracker.attached[0]
        assert title == "Working Session Transcript — run-y"
        assert "# Working Session Transcript" in content
