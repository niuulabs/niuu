"""Helpers for exposing workflow-specific shell shims to CLI transports."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


def _extract_mimir_mount(mcp_servers: list[dict[str, Any]]) -> tuple[str, str] | None:
    for server in mcp_servers:
        args = [str(arg) for arg in server.get("args") or []]
        if len(args) < 5:
            continue
        if args[:3] != ["-m", "mimir", "mcp"]:
            continue
        path_value = ""
        name_value = ""
        for index, arg in enumerate(args):
            if arg == "--path" and index + 1 < len(args):
                path_value = args[index + 1]
            if arg == "--name" and index + 1 < len(args):
                name_value = args[index + 1]
        if path_value:
            return path_value, name_value
    return None


def ensure_codex_tool_shims(
    workspace_dir: str,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> tuple[Path | None, dict[str, str]]:
    servers = list(mcp_servers or [])
    mount = _extract_mimir_mount(servers)
    if mount is None:
        return None, {}

    mimir_path, mimir_name = mount
    workspace = Path(workspace_dir).expanduser().resolve()
    bin_dir = workspace / ".skuld-tools" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    commands = {
        "mimir_ingest": "ingest",
        "mimir_search": "search",
        "mimir_read": "read",
        "mimir_read_source": "read-source",
        "mimir_write": "write",
        "mimir_publish_files": "publish-files",
        "mimir_list": "list",
    }
    for command_name, subcommand in commands.items():
        target = bin_dir / command_name
        target.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    f'exec python3 -m ravn.cli.mimir_bridge {subcommand} "$@"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}" + os.environ.get("PATH", ""),
        "RAVN_WORKSPACE_DIR": str(workspace),
        "RAVN_MIMIR_PATH": mimir_path,
    }
    if mimir_name:
        env["RAVN_MIMIR_NAME"] = mimir_name
    return bin_dir, env
