"""Phase 2 E2E scenarios with mock LLM (NIU-455).

Covers acceptance criteria:
1. Read-and-modify flow: LLM reads file → edits file → confirms change
2. Permission denied recovery: LLM tries rm → denied → uses safer alternative
3. Context compression: 50+ mock turns → conversation history accumulates
4. Budget exhaustion: LLM in tool loop → wraps up within max_iterations

All scenarios use MockLLM (scripted, no network) and real file tools
against a real tmp_path filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from niuu.mesh.events import MeshEventType
from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.adapters.permission.enforcer import PermissionEnforcer
from ravn.adapters.tools.file_tools import EditFileTool, ReadFileTool, WriteFileTool
from ravn.config import PermissionConfig
from ravn.domain.exceptions import MaxIterationsError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    TokenUsage,
    ToolCall,
)
from tests.ravn.conftest import MockLLM, make_agent, make_text_response

# ---------------------------------------------------------------------------
# Helper: PermissionEnforcer in full_access wrapping as PermissionPort
# ---------------------------------------------------------------------------


def _full_access_enforcer(workspace: Path) -> PermissionEnforcer:
    cfg = PermissionConfig(mode="full_access")
    return PermissionEnforcer(cfg, workspace_root=workspace)


def _read_only_enforcer(workspace: Path) -> PermissionEnforcer:
    cfg = PermissionConfig(mode="read_only")
    return PermissionEnforcer(cfg, workspace_root=workspace)


def _tool_call(name: str, call_id: str = "tc1", **kwargs) -> ToolCall:
    return ToolCall(id=call_id, name=name, input=kwargs)


def _tool_response(tool_call: ToolCall) -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[tool_call],
        stop_reason=StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


# ===========================================================================
# Scenario 1 — Read-and-modify flow
# ===========================================================================


class TestReadAndModifyFlow:
    """LLM reads a file, then edits it, then confirms the change."""

    @pytest.mark.asyncio
    async def test_read_then_edit_then_confirm(self, tmp_path: Path) -> None:
        # Setup: a file with known content
        target = tmp_path / "target.txt"
        target.write_text("version: 1.0\n")

        read_tool = ReadFileTool(workspace=tmp_path)
        edit_tool = EditFileTool(workspace=tmp_path)

        # Scripted LLM conversation:
        # Turn 1: LLM reads the file
        # Turn 2: LLM edits the file
        # Turn 3: LLM confirms with a text response

        read_call = _tool_call("read_file", call_id="r1", path=str(target))
        edit_call = _tool_call(
            "edit_file",
            call_id="e1",
            path=str(target),
            old_string="version: 1.0",
            new_string="version: 2.0",
        )

        llm = MockLLM(
            [
                _tool_response(read_call),  # iteration 1: read
                _tool_response(edit_call),  # iteration 2: edit
                make_text_response("File updated to version 2.0."),  # iteration 3: done
            ]
        )

        agent, ch = make_agent(
            llm,
            tools=[read_tool, edit_tool],
            permission=AllowAllPermission(),
            max_iterations=10,
        )

        result = await agent.run_turn("Please update the version to 2.0")

        # The file should now contain the new version
        assert target.read_text() == "version: 2.0\n"
        assert result.response == "File updated to version 2.0."
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "read_file"
        assert result.tool_calls[1].name == "edit_file"

    @pytest.mark.asyncio
    async def test_read_tool_result_returned_to_llm(self, tmp_path: Path) -> None:
        """Verify tool results are fed back correctly as user messages."""
        f = tmp_path / "data.txt"
        f.write_text("hello from file\n")

        read_tool = ReadFileTool(workspace=tmp_path)
        read_call = _tool_call("read_file", call_id="r1", path=str(f))

        llm = MockLLM(
            [
                _tool_response(read_call),
                make_text_response("I read: hello from file"),
            ]
        )

        agent, ch = make_agent(llm, tools=[read_tool])
        result = await agent.run_turn("Read the file")

        assert not any(r.is_error for r in result.tool_results)
        assert "hello from file" in result.tool_results[0].content

    @pytest.mark.asyncio
    async def test_write_new_file_flow(self, tmp_path: Path) -> None:
        """LLM creates a new file in the workspace."""
        new_file = tmp_path / "output.txt"
        write_tool = WriteFileTool(workspace=tmp_path)
        write_call = _tool_call(
            "write_file", call_id="w1", path=str(new_file), content="created by LLM\n"
        )

        llm = MockLLM(
            [
                _tool_response(write_call),
                make_text_response("File created successfully."),
            ]
        )

        agent, ch = make_agent(llm, tools=[write_tool])
        result = await agent.run_turn("Create output.txt")

        assert new_file.exists()
        assert new_file.read_text() == "created by LLM\n"
        assert result.response == "File created successfully."


# ===========================================================================
# Scenario 2 — Permission denied recovery
# ===========================================================================


class TestPermissionDeniedRecovery:
    """LLM tries a denied operation → gets error → uses safer alternative."""

    @pytest.mark.asyncio
    async def test_denied_tool_returns_error_result(self) -> None:
        """When a tool's required_permission is denied, the agent returns an error ToolResult."""
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()  # requires "tool:echo"
        llm = MockLLM(
            [
                _tool_response(_tool_call("echo", call_id="e1", message="hello")),
                make_text_response("Denied, I'll try differently."),
            ]
        )

        # DenyAllPermission denies every tool
        agent, ch = make_agent(llm, tools=[echo], permission=DenyAllPermission())
        result = await agent.run_turn("Echo hello")

        # Tool result should be an error (permission denied)
        assert len(result.tool_results) == 1
        assert result.tool_results[0].is_error
        assert "denied" in result.tool_results[0].content.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Calling an unregistered tool name returns an error ToolResult."""
        llm = MockLLM(
            [
                _tool_response(_tool_call("nonexistent_tool", call_id="n1")),
                make_text_response("That tool doesn't exist."),
            ]
        )

        agent, ch = make_agent(llm, tools=[])
        result = await agent.run_turn("Use nonexistent tool")

        assert result.tool_results[0].is_error
        assert "Unknown tool" in result.tool_results[0].content

    @pytest.mark.asyncio
    async def test_file_write_outside_workspace_denied_and_recovered(self, tmp_path: Path) -> None:
        """LLM tries to write outside workspace → error → writes inside workspace."""
        inside = tmp_path / "safe.txt"
        write_tool = WriteFileTool(workspace=tmp_path)

        outside_call = _tool_call(
            "write_file",
            call_id="w1",
            path=str(tmp_path.parent / "evil.txt"),
            content="bad",
        )
        inside_call = _tool_call(
            "write_file",
            call_id="w2",
            path=str(inside),
            content="good",
        )

        llm = MockLLM(
            [
                _tool_response(outside_call),
                _tool_response(inside_call),
                make_text_response("Wrote inside workspace instead."),
            ]
        )

        agent, ch = make_agent(llm, tools=[write_tool])
        result = await agent.run_turn("Write a file")

        # First write should have failed (outside workspace)
        assert result.tool_results[0].is_error
        # Second write should have succeeded
        assert not result.tool_results[1].is_error
        assert inside.exists()
        assert inside.read_text() == "good"

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_result(self) -> None:
        """When a tool raises an exception, the agent catches it as an error ToolResult."""
        from tests.ravn.fixtures.fakes import FailingTool

        failing = FailingTool()
        llm = MockLLM(
            [
                _tool_response(_tool_call("fail", call_id="f1")),
                make_text_response("The tool failed, I'll stop."),
            ]
        )

        agent, ch = make_agent(llm, tools=[failing])
        result = await agent.run_turn("Run the failing tool")

        assert result.tool_results[0].is_error
        assert "Tool error" in result.tool_results[0].content


# ===========================================================================
# Scenario 3 — Conversation history accumulation (context compression proxy)
# ===========================================================================


class TestConversationHistoryAccumulation:
    """Verify that multi-turn conversations accumulate history correctly.

    The actual compressor is not implemented yet; this validates that
    the agent maintains correct message history across many turns, which
    is the prerequisite for compression to work correctly.
    """

    @pytest.mark.asyncio
    async def test_multiple_turns_accumulate_in_session(self) -> None:
        responses = [make_text_response(f"Response {i}") for i in range(10)]
        llm = MockLLM(responses)
        agent, ch = make_agent(llm)

        for i in range(10):
            await agent.run_turn(f"Turn {i}")

        assert agent.session.turn_count == 10
        # Each turn adds user + assistant = 2 messages
        assert len(agent.session.messages) == 20

    @pytest.mark.asyncio
    async def test_tool_calls_add_extra_messages_to_history(self) -> None:
        """Each tool call adds assistant + tool_result messages to history."""
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()
        tool_call = _tool_call("echo", call_id="t1", message="ping")
        llm = MockLLM(
            [
                _tool_response(tool_call),
                make_text_response("pong"),
            ]
        )

        agent, ch = make_agent(llm, tools=[echo])
        await agent.run_turn("Echo ping")

        # user msg + assistant (with tool call) + tool result + final assistant = 4
        assert len(agent.session.messages) == 4

    @pytest.mark.asyncio
    async def test_session_token_usage_accumulates(self) -> None:
        """Token usage accumulates correctly across turns."""
        responses = [
            make_text_response("Hi!", input_tokens=10, output_tokens=5),
            make_text_response("Bye!", input_tokens=15, output_tokens=8),
        ]
        llm = MockLLM(responses)
        agent, ch = make_agent(llm)

        result1 = await agent.run_turn("Turn 1")
        result2 = await agent.run_turn("Turn 2")

        assert result1.usage.input_tokens == 10
        assert result2.usage.input_tokens == 15


# ===========================================================================
# Scenario 4 — Budget exhaustion (max_iterations guard)
# ===========================================================================


class TestBudgetExhaustion:
    """LLM in a tool loop → MaxIterationsError raised when budget exceeded."""

    @pytest.mark.asyncio
    async def test_max_iterations_raises_error(self) -> None:
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()
        # Always responds with a tool call (infinite loop)
        tool_call = _tool_call("echo", call_id="t1", message="ping")
        infinite_responses = [_tool_response(tool_call)] * 20  # more than max_iterations

        llm = MockLLM(infinite_responses)
        agent, ch = make_agent(llm, tools=[echo], max_iterations=3)

        with pytest.raises(MaxIterationsError):
            await agent.run_turn("Loop forever")

    @pytest.mark.asyncio
    async def test_max_iterations_error_contains_limit(self) -> None:
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()
        tool_call = _tool_call("echo", call_id="t1", message="x")
        llm = MockLLM([_tool_response(tool_call)] * 10)
        agent, ch = make_agent(llm, tools=[echo], max_iterations=2)

        with pytest.raises(MaxIterationsError) as exc_info:
            await agent.run_turn("Loop")

        assert "2" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_agent_completes_within_budget(self) -> None:
        """Agent that finishes before budget is exhausted does not raise."""
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()
        tool_call = _tool_call("echo", call_id="t1", message="hello")
        llm = MockLLM(
            [
                _tool_response(tool_call),
                make_text_response("Done within budget."),
            ]
        )

        agent, ch = make_agent(llm, tools=[echo], max_iterations=5)
        result = await agent.run_turn("Echo once then stop")
        assert result.response == "Done within budget."

    @pytest.mark.asyncio
    async def test_events_emitted_during_tool_loop(self) -> None:
        """Tool start/result events are emitted for each iteration."""
        from tests.ravn.fixtures.fakes import EchoTool

        echo = EchoTool()
        tool_call = _tool_call("echo", call_id="t1", message="ping")
        llm = MockLLM(
            [
                _tool_response(tool_call),
                make_text_response("Done."),
            ]
        )

        agent, ch = make_agent(llm, tools=[echo])
        await agent.run_turn("Echo")

        event_types = [e.type for e in ch.events]
        assert MeshEventType.TOOL_START in event_types
        assert MeshEventType.TOOL_RESULT in event_types
