"""Bash tool — execute shell commands with multi-stage security validation.

Unlike :class:`~ravn.adapters.tools.terminal.TerminalTool` (which keeps a
persistent shell), this tool spawns a **fresh subprocess per call**, runs
the command in the workspace root directory, and gates every command through
:class:`~ravn.adapters.permission.bash_validator.BashValidationPipeline` before
executing.

Features
--------
- Configurable timeout (default 120 s).
- Output truncation to a configurable byte limit (default 100 KiB).
- Exit-code tracking — non-zero exit is reflected in ``is_error``.
- Accumulated validation warnings are prepended to the tool result so the
  agent is aware of any path or mode concerns.
- Working directory: workspace root (defaults to CWD).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from ravn.adapters.permission.bash_validator import BashValidationPipeline
from ravn.config import BashToolConfig
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_BASH = "bash:execute"

_TRUNCATION_NOTICE = "\n[... output truncated ...]"


class BashTool(ToolPort):
    """Execute a bash command with full validation pipeline.

    The tool:

    1. Runs the command through the 5-stage
       :class:`~ravn.adapters.permission.bash_validator.BashValidationPipeline`.
    2. Returns a ``Deny`` error result immediately if any stage blocks
       the command.
    3. Spawns a subprocess (fresh per call) in the workspace root.
    4. Returns combined stdout+stderr, truncated if necessary.
    5. Prepends accumulated warnings to the output.

    Args:
        config:         Optional :class:`~ravn.config.BashToolConfig`
                        instance.  Defaults constructed when *None*.
        workspace_root: Explicit workspace path.  Overrides
                        ``config.workspace_root`` when supplied.
    """

    def __init__(
        self,
        config: BashToolConfig | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        cfg = config or BashToolConfig()
        self._mode = cfg.mode
        self._timeout = cfg.timeout_seconds
        self._max_output_bytes = cfg.max_output_bytes
        self._workspace_root: Path = workspace_root or (
            Path(cfg.workspace_root).resolve() if cfg.workspace_root else Path.cwd()
        )
        self._validator = BashValidationPipeline()

    # ------------------------------------------------------------------
    # ToolPort interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a bash command and return combined stdout/stderr. "
            "Commands are validated through a multi-stage security pipeline "
            "before execution. Each call runs in a fresh subprocess with the "
            "workspace root as the working directory."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command to execute.",
                },
            },
            "required": ["command"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_BASH

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        command = input.get("command", "").strip()
        if not command:
            return ToolResult(tool_call_id="", content="No command provided.", is_error=True)

        result = self._validator.validate(
            command, mode=self._mode, workspace_root=self._workspace_root
        )

        if not result.allowed:
            reason = result.deny_reason or "command denied by validation pipeline"
            logger.warning("bash tool blocked command=%r reason=%s", command, reason)
            return ToolResult(tool_call_id="", content=f"[blocked] {reason}", is_error=True)

        return await self._execute_validated(command, result.warnings)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_validated(self, command: str, warnings: list[str]) -> ToolResult:
        """Spawn a subprocess for *command* and return the result."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self._workspace_root),
                start_new_session=True,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except TimeoutError:
                # The shell may have spawned descendants which inherited its
                # stdout pipe. Killing only the shell leaves communicate()
                # waiting forever for those descendants to close the pipe.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.communicate()
                output = self._build_output(b"", warnings)
                return ToolResult(
                    tool_call_id="",
                    content=output + f"\n[timed out after {self._timeout:.0f}s]",
                    is_error=True,
                )
        except OSError as exc:
            return ToolResult(
                tool_call_id="",
                content=f"execution error: {exc}",
                is_error=True,
            )

        exit_code = proc.returncode or 0
        output = self._build_output(stdout or b"", warnings)

        if exit_code != 0:
            output = (output + f"\n[exit {exit_code}]").strip()

        return ToolResult(
            tool_call_id="",
            content=output,
            is_error=exit_code != 0,
        )

    def _build_output(self, raw: bytes, warnings: list[str]) -> str:
        """Decode, truncate, and prepend any warnings to *raw* output."""
        truncated = False
        if len(raw) > self._max_output_bytes:
            raw = raw[: self._max_output_bytes]
            truncated = True

        text = raw.decode(errors="replace").rstrip("\n")
        if truncated:
            text += _TRUNCATION_NOTICE

        if warnings:
            prefix = "\n".join(f"[warning] {w}" for w in warnings)
            text = f"{prefix}\n{text}" if text else prefix

        return text
