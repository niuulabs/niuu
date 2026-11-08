"""The tool boundary a persona puts around a Claude session.

One value, rendered the same way by every Claude transport (CLI flags for the subprocess,
persistent, sdk-websocket and tmux transports, Agent SDK options for the SDK transport), so a
persona restricts the session identically whichever transport the node runs.

``tools`` is the set of built-in tools the model is offered at all (``None`` leaves the
default set; an empty tuple offers none). ``allowed`` are pre-approved rules, ``disallowed``
are denied rules. With a ``permission_mode`` of ``dontAsk`` everything not pre-approved is
denied, which is what makes a closed persona closed; ``None`` leaves the node's own mode in
place, for a persona that only forbids a few tools.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClaudeToolPolicy:
    tools: tuple[str, ...] | None = None
    allowed: tuple[str, ...] = ()
    disallowed: tuple[str, ...] = ()
    permission_mode: str | None = None
    strict_mcp_config: bool = True

    @property
    def sets_permission_mode(self) -> bool:
        """True when the policy decides the permission mode, replacing the node's."""
        return self.permission_mode is not None

    def cli_args(self) -> list[str]:
        """The Claude CLI flags for this policy."""
        args: list[str] = []
        if self.tools is not None:
            args.extend(["--tools", ",".join(self.tools)])
        if self.strict_mcp_config:
            args.append("--strict-mcp-config")
        if self.permission_mode is not None:
            args.extend(["--permission-mode", self.permission_mode])
        if self.allowed:
            args.extend(["--allowedTools", ",".join(self.allowed)])
        if self.disallowed:
            args.extend(["--disallowedTools", ",".join(self.disallowed)])
        return args

    def sdk_options(self) -> dict[str, object]:
        """The Claude Agent SDK ``ClaudeAgentOptions`` fields for this policy."""
        options: dict[str, object] = {}
        if self.tools is not None:
            options["tools"] = list(self.tools)
        if self.strict_mcp_config:
            options["strict_mcp_config"] = True
        if self.permission_mode is not None:
            options["permission_mode"] = self.permission_mode
        if self.allowed:
            options["allowed_tools"] = list(self.allowed)
        if self.disallowed:
            options["disallowed_tools"] = list(self.disallowed)
        return options
