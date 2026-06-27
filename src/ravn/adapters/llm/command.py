"""Command-backed LLM adapter for opt-in local evals."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from ravn.adapters.process_runner import CommandResult, run_command
from ravn.domain.models import LLMResponse, StopReason, StreamEvent, TokenUsage
from ravn.ports.llm import LLMPort, SystemPrompt

CommandRunner = Callable[..., Awaitable[CommandResult]]


class CommandLLMAdapter(LLMPort):
    """Generate through a local command that reads the prompt on stdin."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        cwd: str | Path | None = None,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
        env_overrides: dict[str, str] | None = None,
        runner: CommandRunner = run_command,
        **_ignored: Any,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._cwd = Path(cwd) if cwd else None
        self._timeout = float(timeout)
        self._env = dict(env or {}) | dict(env_overrides or {})
        self._runner = runner

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        if tools:
            raise RuntimeError("CommandLLMAdapter does not support tools")
        if thinking is not None:
            raise RuntimeError("CommandLLMAdapter does not support thinking")

        argv = [self._command, *self._args]
        try:
            result = await self._runner(
                argv,
                timeout_seconds=self._timeout,
                cwd=self._cwd,
                env=self._env or None,
                input_text=_prompt(
                    system=system,
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                ),
                check=False,
            )
        except TimeoutError as exc:
            raise RuntimeError(f"CommandLLMAdapter command timed out: {self._command}") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"CommandLLMAdapter command failed ({result.returncode}): {self._command}"
                + (f"\n{detail}" if detail else "")
            )
        return LLMResponse(
            content=result.stdout.strip(),
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )

    def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("CommandLLMAdapter does not support streaming")


def _prompt(
    *,
    system: SystemPrompt,
    messages: list[dict],
    model: str,
    max_tokens: int,
) -> str:
    rendered_messages = "\n\n".join(
        f"### {message.get('role', 'user')}\n{message.get('content', '')}" for message in messages
    )
    return (
        "You are being called through Ravn's command LLM adapter.\n\n"
        f"Model hint: {model}\n"
        f"Max tokens hint: {max_tokens}\n\n"
        "## System\n\n"
        f"{_system_text(system)}\n\n"
        "## Messages\n\n"
        f"{rendered_messages}\n"
    )


def _system_text(system: SystemPrompt) -> str:
    if isinstance(system, str):
        return system
    return "\n".join(str(block.get("text", "")) for block in system)
