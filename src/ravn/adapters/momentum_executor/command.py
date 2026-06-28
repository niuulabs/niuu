"""Command-backed Momentum executor handoff adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ravn.adapters.process_runner import CommandResult, run_command
from ravn.ports.momentum_executor import (
    MomentumExecutorInput,
    MomentumExecutorOutput,
)

CommandRunner = Callable[..., Awaitable[CommandResult]]


class CommandMomentumExecutorHandoffAdapter:
    """Send a bounded handoff frame to a local command over stdin."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        label: str = "command-executor",
        context_name: str = "",
        cwd: str | Path | None = None,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
        env_overrides: dict[str, str] | None = None,
        runner: CommandRunner = run_command,
        **_ignored: Any,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._label = label
        self._context_name = context_name or _sanitized_command(command, self._args)
        self._cwd = Path(cwd) if cwd else None
        self._timeout = float(timeout)
        self._env = dict(env or {}) | dict(env_overrides or {})
        self._runner = runner

    async def handoff(self, handoff_input: MomentumExecutorInput) -> MomentumExecutorOutput:
        argv = [self._command, *self._args]
        started = handoff_input.brief_ref
        try:
            result = await self._runner(
                argv,
                timeout_seconds=self._timeout,
                cwd=self._cwd,
                env=self._env or None,
                input_text=handoff_input.input_frame,
                check=False,
            )
        except RuntimeError as exc:
            return MomentumExecutorOutput(
                executor_label=self._label,
                executor_context=self._context_name,
                status="blocked",
                summary=f"Executor command did not complete for {started}.",
                errors=[str(exc)],
                follow_up_recommended="retry",
                raw_metadata={"command": self._context_name},
            )

        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        if result.returncode != 0:
            return MomentumExecutorOutput(
                executor_label=self._label,
                executor_context=self._context_name,
                status="failed",
                summary=f"Executor command failed for {started}.",
                output=stdout,
                errors=[stderr or f"return code {result.returncode}"],
                follow_up_recommended="ask_human",
                raw_metadata={
                    "command": self._context_name,
                    "returncode": result.returncode,
                },
            )
        return MomentumExecutorOutput(
            executor_label=self._label,
            executor_context=self._context_name,
            status="completed",
            summary=_first_line(stdout) or f"Executor command completed for {started}.",
            output=stdout,
            errors=[stderr] if stderr else [],
            follow_up_recommended="reflect" if stdout else "none",
            raw_metadata={
                "command": self._context_name,
                "returncode": result.returncode,
            },
        )


def _sanitized_command(command: str, args: list[str]) -> str:
    return " ".join([Path(command).name, *args]).strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
