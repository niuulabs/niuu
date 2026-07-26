"""Tests for ContextCompressor and CompressionResult (ravn.compression)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.compression import (
    _FALLBACK_DOCUMENT,
    CompressionResult,
    ContextCompressor,
    _extract_content_text,
    _format_memory_anchor,
    _format_todo_anchor,
    _format_transcript,
)
from ravn.domain.models import LLMResponse, Message, StopReason, TodoItem, TodoStatus, TokenUsage
from ravn.ports.llm import LLMPort

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRUCTURED_DOC = (
    "## Active task\n"
    "Summary.\n\n"
    "## Decisions made\n"
    "- None\n\n"
    "## Errors encountered\n"
    "- None\n\n"
    "## Actions completed\n"
    "- None\n\n"
    "## Open questions\n"
    "- None"
)


def _make_llm(summary_text: str = _STRUCTURED_DOC) -> LLMPort:
    llm = MagicMock(spec=LLMPort)
    llm.generate = AsyncMock(
        return_value=LLMResponse(
            content=summary_text,
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=20),
        )
    )
    return llm


def _make_messages(n: int, role_alternating: bool = True) -> list[Message]:
    """Return a list of n simple messages."""
    msgs = []
    for i in range(n):
        role = "user" if (i % 2 == 0 or not role_alternating) else "assistant"
        msgs.append(Message(role=role, content=f"Message {i}"))
    return msgs


def _char_count_for_tokens(tokens: int) -> int:
    """Return chars needed to produce roughly *tokens* tokens."""
    return tokens * 4


def _make_todo(content: str, status: str = "pending") -> TodoItem:
    return TodoItem(id=content[:8], content=content, status=TodoStatus(status))


def _long_doc(active_task: str = "Test.") -> str:
    return (
        f"## Active task\n{active_task}\n\n"
        "## Decisions made\n- None\n\n"
        "## Errors encountered\n- None\n\n"
        "## Actions completed\n- None\n\n"
        "## Open questions\n- None"
    )


# ---------------------------------------------------------------------------
# CompressionResult
# ---------------------------------------------------------------------------


class TestCompressionResult:
    def test_was_compressed_true(self):
        r = CompressionResult(original_count=10, final_count=5, compression_count=1)
        assert r.was_compressed is True

    def test_was_compressed_false(self):
        r = CompressionResult(original_count=10, final_count=10, compression_count=0)
        assert r.was_compressed is False

    def test_defaults(self):
        r = CompressionResult(original_count=5, final_count=5)
        assert r.compression_count == 0
        assert r.removed_message_count == 0


# ---------------------------------------------------------------------------
# ContextCompressor — basic behaviour
# ---------------------------------------------------------------------------


class TestContextCompressorNoCompression:
    @pytest.mark.asyncio
    async def test_below_threshold_no_compression(self):
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100_000,
            compression_threshold=0.5,
        )
        # Very short messages → well below 50 000 tokens
        msgs = _make_messages(4)
        result_msgs, result = await compressor.maybe_compress(msgs)
        assert result_msgs is msgs
        assert not result.was_compressed
        assert result.original_count == 4
        assert result.final_count == 4

    @pytest.mark.asyncio
    async def test_system_tokens_counted(self):
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=20,
            compression_threshold=0.5,  # 10 token threshold
        )
        # 3 messages × ~1 char per token ≈ 3 tokens; system_tokens=0 → no compress
        msgs = [Message(role="user", content="x" * 4)]  # ~1 token
        result_msgs, result = await compressor.maybe_compress(msgs, system_tokens=0)
        assert not result.was_compressed


class TestContextCompressorCompression:
    @pytest.mark.asyncio
    async def test_compresses_when_over_threshold(self):
        llm = _make_llm(_long_doc("Test task."))
        context_window = 200
        threshold = 0.5  # 100 tokens
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=context_window,
            compression_threshold=threshold,
            protect_first=1,
            protect_last=1,
        )
        # Build messages that collectively exceed 100 tokens (400 chars)
        long_content = "x" * 160  # 40 tokens each
        msgs = [
            Message(role="user", content=long_content),
            Message(role="assistant", content=long_content),
            Message(role="user", content=long_content),
            Message(role="assistant", content=long_content),
        ]
        result_msgs, result = await compressor.maybe_compress(msgs)
        assert result.was_compressed
        assert result.compression_count >= 1
        # Protected first (1) + compacted (1) + protected last (1) = 3 messages
        assert len(result_msgs) < len(msgs)
        # Compacted message should contain the placeholder prefix
        assert any("[Compacted context]" in str(m.content) for m in result_msgs)

    @pytest.mark.asyncio
    async def test_protects_first_and_last(self):
        llm = _make_llm(_long_doc())
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,  # Very low to always trigger
            protect_first=2,
            protect_last=2,
        )
        msgs = [
            Message(role="user", content="First 1"),
            Message(role="assistant", content="First 2"),
            Message(role="user", content="Middle A " * 30),
            Message(role="assistant", content="Middle B " * 30),
            Message(role="user", content="Last 1"),
            Message(role="assistant", content="Last 2"),
        ]
        result_msgs, _ = await compressor.maybe_compress(msgs)
        # First two messages preserved
        assert result_msgs[0].content == "First 1"
        assert result_msgs[1].content == "First 2"
        # Last two messages preserved
        assert result_msgs[-1].content == "Last 2"
        assert result_msgs[-2].content == "Last 1"

    @pytest.mark.asyncio
    async def test_no_compression_when_protected_zone_covers_all(self):
        """When protect_first + protect_last >= total messages, no middle to compact."""
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=10,
            compression_threshold=0.01,  # Always triggers
            protect_first=3,
            protect_last=3,
        )
        msgs = _make_messages(4)  # Only 4 messages; protected zones overlap
        result_msgs, result = await compressor.maybe_compress(msgs)
        # No compression possible
        assert result.removed_message_count == 0

    @pytest.mark.asyncio
    async def test_compression_count_increments(self):
        llm = _make_llm(_long_doc())
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=50,
            compression_threshold=0.1,  # Always triggers
            protect_first=1,
            protect_last=1,
        )
        # Build enough messages to trigger multiple compression passes
        msgs = []
        for i in range(20):
            msgs.append(Message(role="user", content="x" * 16))  # 4 tokens each
        result_msgs, result = await compressor.maybe_compress(msgs)
        assert result.compression_count >= 1

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_fallback_document(self):
        llm = MagicMock(spec=LLMPort)
        llm.generate = AsyncMock(side_effect=RuntimeError("API error"))
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=20,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        # Need at least 2 compressible middle messages so removed >= 1
        msgs = [
            Message(role="user", content="x" * 40),  # protected head
            Message(role="assistant", content="x" * 40),  # middle 1
            Message(role="user", content="x" * 40),  # middle 2
            Message(role="assistant", content="x" * 40),  # protected tail
        ]
        result_msgs, result = await compressor.maybe_compress(msgs)
        # Should still compact; document = fallback
        assert result.was_compressed
        compacted_msg = result_msgs[1]  # protected(1) + compacted + protected(1)
        assert "[Compacted context]" in str(compacted_msg.content)
        assert "summary unavailable" in str(compacted_msg.content)


class TestContextCompressorContextWindowLookup:
    def test_model_name_does_not_imply_context_window(self):
        llm = _make_llm()
        c = ContextCompressor(llm, model="claude-sonnet-4-6")
        assert c._context_window == 0

    def test_unknown_model_does_not_get_a_fake_default(self):
        llm = _make_llm()
        c = ContextCompressor(llm, model="gpt-unknown")
        assert c._context_window == 0

    def test_explicit_context_window_override(self):
        llm = _make_llm()
        c = ContextCompressor(llm, model="claude-sonnet-4-6", context_window=50_000)
        assert c._context_window == 50_000

    def test_default_threshold_is_80_percent(self):
        """Default triggers at 80% usage (< 20% remaining)."""
        llm = _make_llm()
        c = ContextCompressor(llm, model="claude-sonnet-4-6")
        assert c._threshold == 0.8

    def test_default_protect_last_is_6(self):
        """Default verbatim recent window is 6 messages (≈ 3 turns)."""
        llm = _make_llm()
        c = ContextCompressor(llm, model="claude-sonnet-4-6")
        assert c._protect_last == 6


# ---------------------------------------------------------------------------
# Intelligent compaction — structured state document
# ---------------------------------------------------------------------------


class TestIntelligentCompaction:
    @pytest.mark.asyncio
    async def test_compacted_message_contains_structured_document(self):
        """The compacted message contains the LLM-generated structured document."""
        doc = (
            "## Active task\n"
            "Implement feature X.\n\n"
            "## Decisions made\n"
            "- Use approach A.\n\n"
            "## Errors encountered\n"
            "- None\n\n"
            "## Actions completed\n"
            "- Read file foo.py\n\n"
            "## Open questions\n"
            "- Should we add tests?"
        )
        llm = _make_llm(doc)
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="Start task"),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="End task"),
        ]
        result_msgs, result = await compressor.maybe_compress(msgs)
        assert result.was_compressed
        compacted = next(m for m in result_msgs if "[Compacted context]" in str(m.content))
        assert "## Active task" in compacted.content
        assert "## Decisions made" in compacted.content
        assert "## Errors encountered" in compacted.content
        assert "## Actions completed" in compacted.content
        assert "## Open questions" in compacted.content

    @pytest.mark.asyncio
    async def test_todos_injected_in_llm_prompt(self):
        """Active todos are passed to the LLM prompt when provided."""
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        todos = [
            _make_todo("Write tests", "in_progress"),
            _make_todo("Update docs", "pending"),
        ]
        await compressor.maybe_compress(msgs, todos=todos)
        call_args = llm.generate.call_args
        user_content = call_args[0][0][0]["content"]
        assert "Write tests" in user_content
        assert "Update docs" in user_content
        assert "in_progress" in user_content

    @pytest.mark.asyncio
    async def test_memory_summary_injected_in_llm_prompt(self):
        """Memory summary is passed to the LLM prompt when provided."""
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        memory = "Previously worked on foo service. Prefers raw SQL."
        await compressor.maybe_compress(msgs, memory_summary=memory)
        call_args = llm.generate.call_args
        user_content = call_args[0][0][0]["content"]
        assert "Previously worked on foo service" in user_content

    @pytest.mark.asyncio
    async def test_no_todos_no_anchor_section(self):
        """When no todos are provided, no todo anchor section appears in prompt."""
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        await compressor.maybe_compress(msgs, todos=None, memory_summary=None)
        call_args = llm.generate.call_args
        user_content = call_args[0][0][0]["content"]
        assert "Active todos" not in user_content
        assert "Episodic memory summary" not in user_content

    @pytest.mark.asyncio
    async def test_compacted_placeholder_wraps_document(self):
        """The compacted message uses the [Compacted context] prefix."""
        doc = _long_doc("X.")
        llm = _make_llm(doc)
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        result_msgs, _ = await compressor.maybe_compress(msgs)
        compacted = result_msgs[1]
        assert compacted.content.startswith("[Compacted context]")
        assert doc in compacted.content

    @pytest.mark.asyncio
    async def test_fallback_document_on_empty_llm_response(self):
        """Empty LLM response falls back to the fallback document."""
        llm = MagicMock(spec=LLMPort)
        llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="",
                tool_calls=[],
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=5, output_tokens=0),
            )
        )
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        result_msgs, result = await compressor.maybe_compress(msgs)
        assert result.was_compressed
        compacted = result_msgs[1]
        assert _FALLBACK_DOCUMENT in compacted.content

    @pytest.mark.asyncio
    async def test_structured_system_prompt_used(self):
        """The intelligent compaction system prompt is used."""
        llm = _make_llm()
        compressor = ContextCompressor(
            llm,
            model="claude-sonnet-4-6",
            context_window=100,
            compression_threshold=0.1,
            protect_first=1,
            protect_last=1,
        )
        msgs = [
            Message(role="user", content="x" * 20),
            Message(role="assistant", content="x" * 40),
            Message(role="user", content="x" * 40),
            Message(role="assistant", content="x" * 20),
        ]
        await compressor.maybe_compress(msgs)
        call_kwargs = llm.generate.call_args[1]
        system_prompt = call_kwargs["system"]
        assert "structured state document" in system_prompt
        assert "## Active task" in system_prompt


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestExtractContentText:
    def test_string_passthrough(self):
        assert _extract_content_text("hello") == "hello"

    def test_text_block(self):
        content = [{"type": "text", "text": "block text"}]
        assert "block text" in _extract_content_text(content)

    def test_tool_use_block(self):
        content = [{"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}}]
        result = _extract_content_text(content)
        assert "tool_call:bash" in result

    def test_tool_result_block(self):
        content = [{"type": "tool_result", "content": "output"}]
        result = _extract_content_text(content)
        assert "output" in result

    def test_unknown_block_ignored(self):
        content = [{"type": "mystery", "data": "stuff"}]
        result = _extract_content_text(content)
        assert result == ""

    def test_non_dict_ignored(self):
        assert _extract_content_text(["not_a_dict"]) == ""  # type: ignore[arg-type]


class TestFormatTranscript:
    def test_basic(self):
        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi"),
        ]
        transcript = _format_transcript(msgs)
        assert "USER: Hello" in transcript
        assert "ASSISTANT: Hi" in transcript

    def test_empty_messages_excluded(self):
        msgs = [
            Message(role="user", content=""),
            Message(role="assistant", content="Response"),
        ]
        transcript = _format_transcript(msgs)
        assert "USER:" not in transcript
        assert "ASSISTANT: Response" in transcript

    def test_list_content_extracted(self):
        content = [{"type": "text", "text": "tool output"}]
        msgs = [Message(role="user", content=content)]
        transcript = _format_transcript(msgs)
        assert "tool output" in transcript


class TestFormatTodoAnchor:
    def test_none_returns_empty(self):
        assert _format_todo_anchor(None) == ""

    def test_empty_list_returns_empty(self):
        assert _format_todo_anchor([]) == ""

    def test_todos_formatted(self):
        todos = [
            _make_todo("Fix bug", "in_progress"),
            _make_todo("Write docs", "pending"),
        ]
        result = _format_todo_anchor(todos)
        assert "Active todos" in result
        assert "Fix bug" in result
        assert "Write docs" in result
        assert "in_progress" in result
        assert "pending" in result

    def test_done_todos_included(self):
        todos = [_make_todo("Completed task", "done")]
        result = _format_todo_anchor(todos)
        assert "Completed task" in result


class TestFormatMemoryAnchor:
    def test_none_returns_empty(self):
        assert _format_memory_anchor(None) == ""

    def test_empty_string_returns_empty(self):
        assert _format_memory_anchor("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _format_memory_anchor("   ") == ""

    def test_memory_summary_formatted(self):
        result = _format_memory_anchor("User prefers typed Python.")
        assert "Episodic memory summary" in result
        assert "User prefers typed Python." in result

    def test_whitespace_stripped(self):
        result = _format_memory_anchor("  summary text  ")
        assert "summary text" in result
        assert result.count("  summary text  ") == 0  # stripped
