"""CliChannel — renders Ravn events to the terminal."""

from __future__ import annotations

import sys

from niuu.mesh.events import MeshEvent, MeshEventType
from ravn.ports.channel import ChannelPort


class CliChannel(ChannelPort):
    """Renders streaming text and tool use/results to stdout.

    Output is written synchronously to avoid interleaving with other async I/O.
    Each event type has a distinct visual treatment so the user can follow
    what the agent is doing in real time.
    """

    def __init__(
        self,
        *,
        file=None,
        result_truncation_limit: int = 500,
        input_value_limit: int = 60,
    ) -> None:
        self._file = file or sys.stdout
        self._in_response = False
        self._result_truncation_limit = result_truncation_limit
        self._input_value_limit = input_value_limit

    async def emit(self, event: MeshEvent) -> None:
        match event.type:
            case MeshEventType.THOUGHT:
                # Stream text deltas inline without a newline.
                print(event.payload["text"], end="", flush=True, file=self._file)
                self._in_response = True

            case MeshEventType.RESPONSE:
                # Final complete response — just print a newline to end the stream.
                if self._in_response:
                    print(file=self._file)
                    self._in_response = False

            case MeshEventType.TOOL_START:
                tool_name = event.payload["tool_name"]
                tool_input = event.payload.get("input", {})
                diff = event.payload.get("diff")
                if self._in_response:
                    print(file=self._file)
                    self._in_response = False
                print(
                    f"\n⟳ {tool_name}({_format_input(tool_input, self._input_value_limit)})",
                    file=self._file,
                )
                if diff:
                    separator = "─" * 33
                    print(separator, file=self._file)
                    print(diff.rstrip(), file=self._file)
                    print(separator, file=self._file)

            case MeshEventType.TOOL_RESULT:
                tool_name = event.payload.get("tool_name", "")
                is_error = event.payload.get("is_error", False)
                prefix = "✗" if is_error else "✓"
                content = event.payload["result"]
                # Truncate very long results for readability.
                if len(content) > self._result_truncation_limit:
                    content = content[: self._result_truncation_limit] + "…"
                print(f"{prefix} {tool_name}: {content}", file=self._file)

            case MeshEventType.ERROR:
                print(f"\n[error] {event.payload['message']}", file=self._file)

    def finish(self) -> None:
        """Ensure the terminal is in a clean state after output."""
        if self._in_response:
            print(file=self._file)
            self._in_response = False


def _format_input(tool_input: dict, value_limit: int = 60) -> str:
    """Format tool input for compact display."""
    if not tool_input:
        return ""
    parts = []
    for key, value in tool_input.items():
        value_str = str(value)
        if len(value_str) > value_limit:
            value_str = value_str[:value_limit] + "…"
        parts.append(f"{key}={value_str!r}")
    return ", ".join(parts)
