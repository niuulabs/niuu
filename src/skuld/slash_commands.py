"""Slash-command enumeration for Skuld coding sessions.

Builds the canonical catalog of slash commands available in a Claude Code
session so the broker can advertise them to web/iOS clients for ``/``
autocomplete, and so a command typed in a client reaches the CLI the right way.

Sources, in priority order:
  1. The CLI's own ``system/init`` event (its ``slash_commands`` + ``skills``
     lists) — the authoritative set of what the running CLI will actually
     accept. The default stream-json transports capture these at startup.
  2. A filesystem scan of project/user ``.claude/commands`` + ``.claude/skills``
     and installed plugin commands — supplies human descriptions and argument
     hints for the commands the CLI reported. It ONLY enriches; it never adds a
     command the engine didn't advertise (that command wouldn't run in this mode).
  3. A curated description table for the built-in commands, which have no
     on-disk manifest.

Canonical command dict (matches the shape the tmux transport already emits,
extended with two additive, optional fields):

    {
        "name": "/compact",          # always leading-slash, lowercased namespace
        "description": "Compact the current conversation",
        "argument_hint": "",         # e.g. "[instructions]"
        "source": "builtin",         # builtin | custom | skill | plugin
    }
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("skuld.slash_commands")

# Curated descriptions for Claude Code's built-in slash commands. Built-ins have
# no on-disk manifest, so this is the only description source for them. Names are
# bare (no leading slash). Unknown built-ins still appear in the catalog (from
# the init event) — they just carry an empty description.
BUILTIN_COMMANDS: dict[str, str] = {
    "add-dir": "Add a working directory to the session",
    "agents": "Manage agent teams and subagents",
    "bug": "Report a bug to Anthropic",
    "clear": "Clear the conversation history",
    "compact": "Compact the current conversation to free up context",
    "config": "Open the configuration panel",
    "context": "Inspect context window usage",
    "cost": "Show token usage and cost for this session",
    "doctor": "Check Claude Code health and configuration",
    "exit": "Exit Claude Code",
    "export": "Export the current conversation",
    "help": "Show available commands and help",
    "hooks": "Configure lifecycle hooks",
    "init": "Initialize a CLAUDE.md for this project",
    "login": "Authenticate Claude Code",
    "logout": "Sign out of Claude Code",
    "mcp": "Manage MCP servers",
    "memory": "Edit memory files",
    "model": "Switch the active model",
    "output-style": "Change the output style",
    "permissions": "Manage tool permissions",
    "pr-comments": "Fetch and act on pull-request comments",
    "release-notes": "Show Claude Code release notes",
    "resume": "Resume a prior session",
    "review": "Review code changes",
    "status": "Show Claude Code status",
    "terminal-setup": "Configure terminal key bindings",
    "vim": "Toggle Vim editing mode",
}

_FRONTMATTER_FENCE = "---"


def _normalize_name(raw: str) -> str:
    """Return a bare command name: no leading slash, stripped, lowercased."""
    return raw.strip().lstrip("/").strip().lower()


def _slash(name: str) -> str:
    """Return the display name with a single leading slash."""
    bare = _normalize_name(name)
    return f"/{bare}" if bare else ""


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a leading ``---`` YAML frontmatter block. Returns {} if absent or
    malformed — a missing/bad header must never break enumeration."""
    if not text.startswith(_FRONTMATTER_FENCE):
        return {}
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_FENCE:
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        parsed = yaml.safe_load(block)
    except Exception:
        logger.debug("Failed to parse frontmatter block", exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Could not read %s", path, exc_info=True)
        return ""


def _command_from_markdown(path: Path, name: str, source: str) -> dict[str, Any]:
    """Build a catalog dict from a markdown command/skill file's frontmatter."""
    meta = _parse_frontmatter(_read_text(path))
    fm_name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    description = meta.get("description")
    if not isinstance(description, str):
        description = ""
    # Collapse multi-line (block-scalar) descriptions to a single tidy line.
    description = " ".join(description.split()).strip()
    hint = meta.get("argument-hint") or meta.get("argument_hint") or ""
    if not isinstance(hint, str):
        hint = ""
    return {
        "name": _slash(fm_name or name),
        "description": description,
        "argument_hint": hint.strip(),
        "source": source,
    }


def _iter_markdown(root: Path) -> list[tuple[Path, str]]:
    """Yield (path, namespaced-name) for every ``*.md`` under ``root``.

    Nested directories namespace the command with ``:`` separators, matching
    Claude Code's ``/namespace:command`` convention.
    """
    out: list[tuple[Path, str]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).with_suffix("")
        name = ":".join(rel.parts)
        out.append((path, name))
    return out


def _scan_commands_dir(root: Path, source: str) -> list[dict[str, Any]]:
    return [_command_from_markdown(p, name, source) for p, name in _iter_markdown(root)]


def _scan_skills_dir(root: Path, source: str = "skill") -> list[dict[str, Any]]:
    """Skills come in two on-disk shapes: ``skills/<name>/SKILL.md`` (directory)
    or ``skills/<name>.md`` (flat file). Handle both."""
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                out.append(_command_from_markdown(skill_md, entry.name, source))
        elif entry.suffix == ".md":
            out.append(_command_from_markdown(entry, entry.stem, source))
    return out


def _scan_plugin_commands(plugins_root: Path) -> list[dict[str, Any]]:
    """Best-effort scan of installed plugin commands. Plugins are namespaced by
    their plugin directory name (``/<plugin>:<command>``)."""
    out: list[dict[str, Any]] = []
    marketplaces = plugins_root / "marketplaces"
    if not marketplaces.is_dir():
        return out
    for cmd_dir in sorted(marketplaces.glob("*/plugins/*/commands")):
        if not cmd_dir.is_dir():
            continue
        plugin = cmd_dir.parent.name
        for path, name in _iter_markdown(cmd_dir):
            entry = _command_from_markdown(path, f"{plugin}:{name}", "plugin")
            out.append(entry)
    return out


def enumerate_filesystem_commands(workspace_dir: str | None) -> list[dict[str, Any]]:
    """Scan project + user + plugin command/skill files for the active workspace.

    Never raises: any unreadable directory is skipped. Returns canonical command
    dicts; later callers dedupe by name.
    """
    home = Path.home() / ".claude"
    results: list[dict[str, Any]] = []

    roots: list[tuple[Path, str]] = []
    if workspace_dir:
        ws = Path(workspace_dir) / ".claude"
        roots.append((ws / "commands", "custom"))
    roots.append((home / "commands", "custom"))

    for root, source in roots:
        try:
            results.extend(_scan_commands_dir(root, source))
        except Exception:
            logger.debug("command scan failed for %s", root, exc_info=True)

    skill_roots: list[Path] = []
    if workspace_dir:
        skill_roots.append(Path(workspace_dir) / ".claude" / "skills")
    skill_roots.append(home / "skills")
    for root in skill_roots:
        try:
            results.extend(_scan_skills_dir(root))
        except Exception:
            logger.debug("skill scan failed for %s", root, exc_info=True)

    try:
        results.extend(_scan_plugin_commands(home / "plugins"))
    except Exception:
        logger.debug("plugin scan failed", exc_info=True)

    return results


def build_slash_command_catalog(
    slash_commands: list[str] | None,
    skills: list[str] | None,
    workspace_dir: str | None,
    *,
    include_filesystem: bool = True,
) -> list[dict[str, Any]]:
    """Merge the CLI-reported command/skill names with filesystem details and
    built-in descriptions into a single deduped, sorted catalog.

    ``slash_commands`` / ``skills`` are the bare-name lists from the CLI's
    ``system/init`` event and are the SOLE source of which commands exist — they
    are exactly what this session mode will actually run. The filesystem scan and
    built-in table only ENRICH descriptions / argument hints for those reported
    commands; they NEVER add a command the engine didn't advertise (an unreported
    command wouldn't execute in this mode, so we must not surface it).
    """
    by_name: dict[str, dict[str, Any]] = {}

    def _ensure(name: str, source: str) -> dict[str, Any]:
        bare = _normalize_name(name)
        if not bare:
            return {}
        entry = by_name.get(bare)
        if entry is None:
            entry = {
                "name": f"/{bare}",
                "description": BUILTIN_COMMANDS.get(bare, ""),
                "argument_hint": "",
                "source": source,
            }
            by_name[bare] = entry
        return entry

    # 1. Authoritative existence from the CLI init event.
    for name in slash_commands or []:
        if isinstance(name, str):
            _ensure(name, "builtin" if _normalize_name(name) in BUILTIN_COMMANDS else "custom")
    for name in skills or []:
        if isinstance(name, str):
            entry = _ensure(name, "skill")
            if entry:
                entry["source"] = "skill"

    # 2. Filesystem ENRICHMENT ONLY — attach descriptions/hints to commands the
    # CLI reported. A filesystem command the CLI did NOT advertise is omitted: it
    # wouldn't run in this mode, so surfacing it would be a broken affordance.
    if include_filesystem:
        fs_by_name: dict[str, dict[str, Any]] = {}
        for fs in enumerate_filesystem_commands(workspace_dir):
            bare = _normalize_name(fs["name"])
            if bare and bare not in fs_by_name:
                fs_by_name[bare] = fs
        for bare, entry in by_name.items():
            fs = fs_by_name.get(bare)
            if not fs:
                continue
            if not entry.get("description") and fs.get("description"):
                entry["description"] = fs["description"]
            if not entry.get("argument_hint") and fs.get("argument_hint"):
                entry["argument_hint"] = fs["argument_hint"]
            # Filesystem source is more specific than the "builtin/custom" guess.
            if entry.get("source") in ("custom", "builtin") and fs.get("source"):
                entry["source"] = fs["source"]

    return sorted(by_name.values(), key=lambda c: c["name"])


def compose_slash_command_text(command: str, arguments: str = "") -> str:
    """Compose the literal text to feed a stream-json CLI for a slash command.

    ``/compact`` + ``"keep tests"`` -> ``"/compact keep tests"``. The CLI
    interprets the leading slash and runs the command.
    """
    name = command.strip()
    if not name:
        return ""
    if not name.startswith("/"):
        name = f"/{name}"
    args = (arguments or "").strip()
    return f"{name} {args}".strip()
