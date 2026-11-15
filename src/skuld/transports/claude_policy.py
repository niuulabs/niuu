"""The boundary a persona's permission mode puts around a Claude session.

One value, rendered the same way by every Claude transport (CLI flags for the subprocess,
persistent, sdk-websocket and tmux transports, Agent SDK options for the SDK transport), so a
persona's mode bounds the session identically whichever transport the node runs.

``tools`` is the set of built-in tools the model is offered at all (``None`` leaves the
default set; an empty tuple offers none). ``allowed`` are pre-approved rules. With a
``permission_mode`` of ``dontAsk`` everything not pre-approved is denied, which is what makes
the boundary closed.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Everything not pre-approved is denied.
CLOSED_PERMISSION_MODE = "dontAsk"


@dataclass(frozen=True)
class ClaudeToolPolicy:
    tools: tuple[str, ...] | None = None
    allowed: tuple[str, ...] = ()
    permission_mode: str = CLOSED_PERMISSION_MODE
    strict_mcp_config: bool = True

    def cli_args(self) -> list[str]:
        """The Claude CLI flags for this policy."""
        args: list[str] = []
        if self.tools is not None:
            args.extend(["--tools", ",".join(self.tools)])
        if self.strict_mcp_config:
            args.append("--strict-mcp-config")
        args.extend(["--permission-mode", self.permission_mode])
        if self.allowed:
            args.extend(["--allowedTools", ",".join(self.allowed)])
        return args

    def sdk_options(self) -> dict[str, object]:
        """The Claude Agent SDK ``ClaudeAgentOptions`` fields for this policy."""
        options: dict[str, object] = {}
        if self.tools is not None:
            options["tools"] = list(self.tools)
        if self.strict_mcp_config:
            options["strict_mcp_config"] = True
        options["permission_mode"] = self.permission_mode
        if self.allowed:
            options["allowed_tools"] = list(self.allowed)
        return options
