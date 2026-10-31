"""Broker-provided session capabilities every transport wires into its agent.

The broker builds one :class:`SessionTools` per start and hands it to whichever
transport it constructs. A transport uses it to:

* export :attr:`SessionTools.env` into the agent process (the present-file shim's
  URL and the loopback token *file*),
* prepend :attr:`SessionTools.instructions` to the prompt it gives the agent,
* write generated engine configs (Claude MCP config, hook settings) into the
  broker-owned :attr:`SessionTools.runtime_dir` instead of the workspace, and
* authenticate Claude HTTP hooks with :attr:`SessionTools.loopback_authorization`.

Transports constructed without one (unit tests, harnesses) keep their previous
behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skuld.session_runtime import write_private_file
from skuld.transports.mcp_config import build_claude_mcp_payload

PRESENT_FILE_URL_ENV = "FORGE_PRESENT_FILE_URL"
BROKER_TOKEN_FILE_ENV = "FORGE_BROKER_TOKEN_FILE"
CLAUDE_MCP_CONFIG_FILE = "claude-mcp.json"

PRESENT_FILE_INSTRUCTION = """\
FILE DELIVERY: when you produce a file the user should SEE or open (a report, image, PDF, diagram, \
screenshot, chart, or build artifact), run `present-file <path> [--caption "…"] [--title "…"]`. It \
surfaces the file in the user's app as a tappable card that opens in the file preview, and accepts \
ANY path, including scratch files outside the workspace (e.g. /tmp). Use it for finished \
deliverables the user would want to open — not for routine tool output or intermediate files."""

FORGE_NOTIFY_INSTRUCTION = """\
NOTIFICATIONS: the `{server}` MCP server's `notify` tool sends the user a notification about this \
session (inline in the chat, in the Forge Activities feed and, through their rules, on their \
phone). Notify only when the user would want to be interrupted: when the task they asked for is \
done (kind=milestone, title = the outcome; Forge sends nothing when a turn ends, so this is their \
"done" signal), when you need them (kind=attention to unblock or approve, kind=decision to choose, \
with your recommendation), and when a failure stops the work (kind=error). Never notify for turn \
ends, chat replies, plans or progress; if in doubt, don't. Title: the outcome or the ask in one \
plain line; body: at most one short sentence of new context. Link artifacts (PRs, presented \
files). The same server's `environment` tool describes where this session runs."""


FLOCK_BROKER_INSTRUCTION = """\
RAVN FLOCK: this session brokers a flock of Ravn agents, and they, not you, do the flock's work. \
When the user asks a flock member (by persona, e.g. "the coder", "the reviewer") to do something, \
hand it over with the `{server}` MCP server's `flock_delegate` tool (`flock_members` lists them) \
instead of doing it yourself or with sub-agents; write the task in full. Members that consume \
another's outcome react on their own, so a change handed to the coder reaches the reviewer \
without a second hand-off. Follow progress with `flock_outcomes`, passing the cursor each call \
returns, until the outcome you need arrives or a member reports a failure. Report only verdicts \
and results `flock_outcomes` returned, naming the member that produced each; if it returned \
none, say no member has produced one yet. Never present your own work or judgment as a \
member's."""


def capability_instructions(*, forge_mcp_server: str | None, flock_broker: bool = False) -> str:
    """The capability text for a session.

    present-file always; notify when the MCP is on; flock delegation when the MCP
    is on and the session brokers a Ravn flock.
    """
    parts = [PRESENT_FILE_INSTRUCTION]
    if forge_mcp_server:
        parts.append(FORGE_NOTIFY_INSTRUCTION.format(server=forge_mcp_server))
    if forge_mcp_server and flock_broker:
        parts.append(FLOCK_BROKER_INSTRUCTION.format(server=forge_mcp_server))
    return "\n\n".join(parts)


@dataclass(frozen=True)
class SessionTools:
    """What the broker offers the agent for this start."""

    runtime_dir: Path
    env: Mapping[str, str] = field(default_factory=dict)
    instructions: str = ""
    loopback_authorization: str = ""
    claude_plugin_dirs: tuple[Path, ...] = ()
    codex_skill_roots: tuple[Path, ...] = ()

    def write_claude_mcp_config(self, payload: dict[str, Any] | None) -> Path | None:
        """Write Claude's ``--mcp-config`` document (0600) and return its path."""
        if payload is None:
            return None
        path = self.runtime_dir / CLAUDE_MCP_CONFIG_FILE
        write_private_file(path, json.dumps(payload, indent=2))
        return path


def compose_prompt(*parts: str) -> str:
    """Join non-empty prompt sections with a blank line."""
    return "\n\n".join(part for part in parts if part)


def claude_cli_args(
    tools: SessionTools | None,
    *,
    mcp_servers: list[dict[str, Any]],
    legacy_mcp_config: str | None,
    system_prompt: str,
    include_system_prompt: bool,
) -> list[str]:
    """Claude CLI flags for capability text, MCP servers and session plugins.

    With broker SessionTools the MCP config is a 0600 file in the runtime dir and the
    capability instructions are appended even on ``--resume`` (appended prompts do not
    persist across invocations); without them the legacy inline JSON is kept.
    """
    args: list[str] = []
    appended = compose_prompt(
        tools.instructions if tools else "",
        system_prompt if include_system_prompt else "",
    )
    if appended:
        args.extend(["--append-system-prompt", appended])
    mcp_config = legacy_mcp_config
    if tools is not None:
        path = tools.write_claude_mcp_config(build_claude_mcp_payload(mcp_servers))
        mcp_config = str(path) if path else None
    if mcp_config:
        args.extend(["--mcp-config", mcp_config])
    for plugin_dir in tools.claude_plugin_dirs if tools else ():
        args.extend(["--plugin-dir", str(plugin_dir)])
    return args
