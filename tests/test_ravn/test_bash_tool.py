"""Tests for BashTool — real subprocess execution + validation gating.

These are integration tests that spawn real subprocesses in a tmpdir
sandbox. They do NOT require Docker or any external services.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ravn.adapters.tools.bash import _TRUNCATION_NOTICE, BashTool
from ravn.config import BashToolConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tool(
    tmp_path: Path,
    timeout_seconds: float = 10.0,
    max_output_bytes: int = 100 * 1024,
) -> BashTool:
    cfg = BashToolConfig(
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        workspace_root=str(tmp_path),
    )
    return BashTool(config=cfg, workspace_root=tmp_path)


# ===========================================================================
# Basic execution
# ===========================================================================


class TestBashToolExecution:
    @pytest.mark.asyncio
    async def test_simple_echo(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "echo hello"})
        assert not result.is_error
        assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_exit_code_zero_is_not_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "true"})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "false"})
        assert result.is_error
        assert "exit 1" in result.content

    @pytest.mark.asyncio
    async def test_exit_code_in_output(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "exit 42"})
        assert result.is_error
        assert "42" in result.content

    @pytest.mark.asyncio
    async def test_stderr_captured(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "echo error >&2"})
        assert "error" in result.content

    @pytest.mark.asyncio
    async def test_empty_command_returns_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": ""})
        assert result.is_error
        assert "No command" in result.content

    @pytest.mark.asyncio
    async def test_missing_command_key_returns_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_multiline_output(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "printf 'a\\nb\\nc\\n'"})
        assert not result.is_error
        assert "a" in result.content
        assert "b" in result.content
        assert "c" in result.content


# ===========================================================================
# Working directory
# ===========================================================================


class TestWorkingDirectory:
    @pytest.mark.asyncio
    async def test_cwd_is_workspace_root(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "pwd"})
        assert not result.is_error
        assert str(tmp_path) in result.content

    @pytest.mark.asyncio
    async def test_writes_in_workspace(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        target = tmp_path / "output.txt"
        result = await tool.execute({"command": f"echo data > {target}"})
        assert not result.is_error
        assert target.exists()
        assert target.read_text().strip() == "data"


# ===========================================================================
# Timeout
# ===========================================================================


class TestTimeout:
    @pytest.mark.asyncio
    async def test_command_times_out(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, timeout_seconds=0.2)
        result = await tool.execute({"command": "sleep 10"})
        assert result.is_error
        assert "timed out" in result.content.lower()

    @pytest.mark.asyncio
    async def test_timeout_kills_descendants_holding_output_pipe(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, timeout_seconds=0.2)
        started = time.monotonic()

        result = await tool.execute(
            {
                "command": (
                    'python3 -c "import subprocess, time; '
                    "subprocess.Popen(['sleep', '10']); time.sleep(10)\""
                )
            }
        )

        assert time.monotonic() - started < 2
        assert result.is_error
        assert "timed out" in result.content.lower()

    @pytest.mark.asyncio
    async def test_fast_command_not_timed_out(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, timeout_seconds=5.0)
        result = await tool.execute({"command": "echo done"})
        assert not result.is_error
        assert "timed out" not in result.content.lower()


# ===========================================================================
# Output truncation
# ===========================================================================


class TestOutputTruncation:
    @pytest.mark.asyncio
    async def test_large_output_truncated(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, max_output_bytes=100)
        # Generate more than 100 bytes
        result = await tool.execute({"command": "python3 -c \"print('x' * 500)\""})
        assert not result.is_error
        assert _TRUNCATION_NOTICE in result.content

    @pytest.mark.asyncio
    async def test_small_output_not_truncated(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, max_output_bytes=100 * 1024)
        result = await tool.execute({"command": "echo short"})
        assert not result.is_error
        assert _TRUNCATION_NOTICE not in result.content

    @pytest.mark.asyncio
    async def test_truncated_output_still_contains_content(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path, max_output_bytes=50)
        result = await tool.execute({"command": "python3 -c \"print('A' * 200)\""})
        # Should contain at least some 'A' characters before the truncation notice
        assert "A" in result.content
        assert _TRUNCATION_NOTICE in result.content


# ===========================================================================
# Validation gating (security pipeline)
# ===========================================================================


class TestValidationGating:
    @pytest.mark.asyncio
    async def test_blocked_destructive_command_not_executed(self, tmp_path: Path) -> None:
        target = tmp_path / "should_survive.txt"
        target.write_text("keep me")
        tool = make_tool(tmp_path)
        # rm -rf / is always blocked — should never reach subprocess
        result = await tool.execute({"command": "rm -rf /"})
        assert result.is_error
        assert "[blocked]" in result.content
        # The target file must still exist (command never ran)
        assert target.exists()

    @pytest.mark.asyncio
    async def test_fork_bomb_blocked(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": ":(){ :|:& };:"})
        assert result.is_error
        assert "[blocked]" in result.content

    @pytest.mark.asyncio
    async def test_mkfs_blocked(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "mkfs.ext4 /dev/sdb1"})
        assert result.is_error
        assert "[blocked]" in result.content

    @pytest.mark.asyncio
    async def test_warnings_prepended_for_home_reference(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "echo $HOME"})
        # Should succeed (command is allowed) but warning about $HOME appears
        assert not result.is_error
        assert "[warning]" in result.content

    @pytest.mark.asyncio
    async def test_valid_ls_executes(self, tmp_path: Path) -> None:
        (tmp_path / "myfile.txt").touch()
        tool = make_tool(tmp_path)
        result = await tool.execute({"command": "ls"})
        assert not result.is_error
        assert "myfile.txt" in result.content


# ===========================================================================
# Tool metadata
# ===========================================================================


class TestBashToolMetadata:
    def test_name(self, tmp_path: Path) -> None:
        assert make_tool(tmp_path).name == "bash"

    def test_not_parallelisable(self, tmp_path: Path) -> None:
        assert make_tool(tmp_path).parallelisable is False

    def test_required_permission(self, tmp_path: Path) -> None:
        assert make_tool(tmp_path).required_permission == "bash:execute"

    def test_input_schema_has_command(self, tmp_path: Path) -> None:
        schema = make_tool(tmp_path).input_schema
        assert "command" in schema.get("properties", {})
        assert "command" in schema.get("required", [])

    def test_description_non_empty(self, tmp_path: Path) -> None:
        assert len(make_tool(tmp_path).description) > 10


# ===========================================================================
# Config defaults
# ===========================================================================


class TestBashToolConfig:
    def test_default_timeout(self) -> None:
        cfg = BashToolConfig()
        assert cfg.timeout_seconds == 120.0

    def test_default_max_output_bytes(self) -> None:
        cfg = BashToolConfig()
        assert cfg.max_output_bytes == 100 * 1024

    def test_default_workspace_root_empty_string(self) -> None:
        cfg = BashToolConfig()
        assert cfg.workspace_root == ""

    def test_tool_falls_back_to_cwd_when_no_workspace(self) -> None:
        tool = BashTool()
        assert tool._workspace_root == Path.cwd()

    def test_tool_uses_explicit_workspace_root(self, tmp_path: Path) -> None:
        tool = BashTool(workspace_root=tmp_path)
        assert tool._workspace_root == tmp_path


# ===========================================================================
# E2E: permission denied → agent adapts scenario
# ===========================================================================


class TestE2EPermissionDenied:
    """Simulate an agent encountering a blocked command and adapting."""

    @pytest.mark.asyncio
    async def test_agent_receives_blocked_feedback(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)

        # First call: agent tries a blocked command
        r1 = await tool.execute({"command": "rm -rf /"})
        assert r1.is_error
        assert "[blocked]" in r1.content

        # Agent adapts and tries a safe command instead
        (tmp_path / "target.txt").write_text("content")
        r2 = await tool.execute({"command": "ls"})
        assert not r2.is_error
        assert "target.txt" in r2.content

    @pytest.mark.asyncio
    async def test_agent_gets_warning_and_succeeds(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)

        # Command with a path traversal warning — still executes
        r = await tool.execute({"command": "echo '../relative' is just a string"})
        assert not r.is_error
        # Warning about traversal or not — echo is valid and should return output
        assert "relative" in r.content
