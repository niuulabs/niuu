"""Materialize the ``forge-notify`` skill for engines that load per-session skills.

The skill is written into the broker's runtime directory — never into the
workspace, ``~/.claude`` or ``~/.codex``:

* Claude Code: a session plugin (``.claude-plugin/plugin.json`` +
  ``skills/forge-notify/SKILL.md``) passed with ``--plugin-dir``.
* Codex app-server: a skills root (``forge-notify/SKILL.md``) registered with
  ``skills/extraRoots/set``.
"""

from __future__ import annotations

import json
from pathlib import Path

from niuu.forge_mcp.guidance import (
    COMPACT_NOTIFICATION_GUIDANCE,
    NOTIFICATION_TRIGGER_GUIDANCE,
)
from skuld.session_runtime import write_private_file

FORGE_NOTIFY_SKILL = "forge-notify"
CLAUDE_PLUGIN_NAME = "forge"
_CLAUDE_PLUGIN_DIR = "claude-plugin"
_CODEX_SKILLS_DIR = "codex-skills"
_SKILL_FILE = "SKILL.md"
_PRIVATE_DIR_MODE = 0o700

_SKILL_TEMPLATE = """\
---
name: {skill}
description: When and how to notify the user about this Forge session with the {server} MCP \
notify tool — milestones, decisions, blockers and failures, briefly, without progress spam.
---

# Notifying the user from a Forge session

The `{server}` MCP server's `notify` tool puts a notification inline in this conversation, in
the Forge Notifications feed (every session, every host) and — through the user's own delivery
rules — on their phone or chat. It is how a long-running session gets the user's attention.

## When to notify

{trigger_guidance}

## How to write one

{compact_guidance}

- **Links**: attach the artifact — a PR or CI URL (`kind: pr` / `url`), a file you handed over
  with `present-file` (`kind: file`, `file_id` from its output), another session (`kind: session`).
- **correlation_id**: reuse one id (e.g. the PR number) for follow-ups about the same thing.

## Do not

- Send progress updates or narrate routine steps. One good notification beats five chatty ones.
- Notify just because your turn is ending. Send the `milestone` when the task is actually done.
- Resend a notification whose `state` came back `pending`: it is stored in the session's
  durable log and reaches Forge by itself once Forge is reachable.

`list_notifications` shows what was already reported (avoid duplicates); `environment` tells you
which session, host, project and model you are running as.
"""


def forge_notify_skill_markdown(server_name: str) -> str:
    return _SKILL_TEMPLATE.format(
        skill=FORGE_NOTIFY_SKILL,
        server=server_name,
        trigger_guidance=NOTIFICATION_TRIGGER_GUIDANCE,
        compact_guidance=COMPACT_NOTIFICATION_GUIDANCE,
    )


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    return path


def materialize_claude_plugin(runtime_dir: Path, server_name: str) -> Path:
    """Write the per-session Claude plugin and return its root (for ``--plugin-dir``)."""
    root = _private_dir(runtime_dir / _CLAUDE_PLUGIN_DIR)
    manifest = {
        "name": CLAUDE_PLUGIN_NAME,
        "version": "1.0.0",
        "description": "Forge session capabilities (notifications) injected by Skuld.",
        "author": {"name": "Forge"},
    }
    write_private_file(_private_dir(root / ".claude-plugin") / "plugin.json", json.dumps(manifest))
    skill_dir = _private_dir(root / "skills" / FORGE_NOTIFY_SKILL)
    write_private_file(skill_dir / _SKILL_FILE, forge_notify_skill_markdown(server_name))
    return root


def materialize_codex_skills_root(runtime_dir: Path, server_name: str) -> Path:
    """Write a Codex skills root holding ``forge-notify`` and return it (an extra root)."""
    root = _private_dir(runtime_dir / _CODEX_SKILLS_DIR)
    skill_dir = _private_dir(root / FORGE_NOTIFY_SKILL)
    write_private_file(skill_dir / _SKILL_FILE, forge_notify_skill_markdown(server_name))
    return root
