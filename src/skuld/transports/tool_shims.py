"""Helpers for exposing workflow-specific shell shims to CLI transports."""

from __future__ import annotations

import os
import shlex
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


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


def _ravn_config_has_mimir(config_path: str) -> bool:
    if not config_path:
        return False
    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    mimir = payload.get("mimir")
    return isinstance(mimir, dict) and any(bool(value) for value in mimir.values())


# The `present-file` command — an ENGINE-AGNOSTIC (claude + codex) PATH shim that hands the user a
# file in the Lexi app (in-app SendUserFile). It POSTs the resolved absolute path to the broker's
# loopback endpoint (URL from FORGE_PRESENT_FILE_URL, exported by every transport that installs the
# shims) with the broker's per-start secret read from FORGE_BROKER_TOKEN_FILE; the broker stages
# + emits the card. A pure-stdlib python3 script → no curl/jq dependency, no bash-quoting fragility.
_PRESENT_FILE_SHIM = '''#!/usr/bin/env python3
"""present-file <path> [--caption ...] [--title ...] — show a file to the user in Lexi."""
import json, os, sys, urllib.request, urllib.error


def main() -> None:
    args = sys.argv[1:]
    path = caption = title = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--caption":
            caption = args[i + 1] if i + 1 < len(args) else None; i += 2
        elif a == "--title":
            title = args[i + 1] if i + 1 < len(args) else None; i += 2
        elif a.startswith("--caption="):
            caption = a.split("=", 1)[1]; i += 1
        elif a.startswith("--title="):
            title = a.split("=", 1)[1]; i += 1
        else:
            path = a; i += 1
    if not path:
        print('usage: present-file <path> [--caption "..."] [--title "..."]', file=sys.stderr)
        sys.exit(2)
    url = os.environ.get("FORGE_PRESENT_FILE_URL")
    if not url:
        print("present-file: not available in this session", file=sys.stderr)
        sys.exit(3)
    headers = {"content-type": "application/json"}
    token_file = os.environ.get("FORGE_BROKER_TOKEN_FILE")
    if token_file:
        # The broker's per-start loopback secret; read from the 0600 file, never from argv/env.
        try:
            with open(token_file, encoding="utf-8") as handle:
                headers["authorization"] = "Bearer " + handle.read().strip()
        except OSError as e:
            print("present-file: cannot read the broker token:", e, file=sys.stderr)
            sys.exit(3)
    payload = {"path": os.path.abspath(os.path.expanduser(path))}
    if caption:
        payload["caption"] = caption
    if title:
        payload["title"] = title
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            sys.stdout.write(r.read().decode()); sys.stdout.write("\\n")
    except urllib.error.HTTPError as e:
        sys.stderr.write(e.read().decode()); sys.stderr.write("\\n"); sys.exit(1)
    except Exception as e:  # noqa: BLE001 - surface any transport error to the agent
        print("present-file:", e, file=sys.stderr); sys.exit(1)


main()
'''


def _install_present_file_shim(bin_dir: Path) -> None:
    """Write the engine-agnostic ``present-file`` PATH shim into ``bin_dir``."""
    _install_executable(bin_dir, "present-file", _PRESENT_FILE_SHIM)


# The `forge-describe` command — engine-agnostic like present-file. It sets the session's
# agent-maintained descriptions through the broker's loopback Forge MCP route (URL from
# FORGE_DESCRIBE_URL, exported only when the broker serves the Forge MCP), so the broker's
# session token — never anything the agent holds — authorizes the Forge write.
_FORGE_DESCRIBE_SHIM = '''#!/usr/bin/env python3
"""forge-describe --short "..." [--long "..." | --long-file <path>] — say what this session does."""
import json, os, sys, urllib.request, urllib.error

USAGE = 'usage: forge-describe [--short "..."] [--long "..." | --long-file <path|->]'


def _value(args, i, flag):
    if i + 1 >= len(args):
        print("forge-describe: " + flag + " needs a value\\n" + USAGE, file=sys.stderr)
        sys.exit(2)
    return args[i + 1]


def main() -> None:
    args = sys.argv[1:]
    payload = {}
    long_file = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(USAGE + "\\nAn empty value clears that description.")
            return
        if a == "--short":
            payload["short_description"] = _value(args, i, a); i += 2
        elif a == "--long":
            payload["description"] = _value(args, i, a); i += 2
        elif a == "--long-file":
            long_file = _value(args, i, a); i += 2
        elif a.startswith("--short="):
            payload["short_description"] = a.split("=", 1)[1]; i += 1
        elif a.startswith("--long="):
            payload["description"] = a.split("=", 1)[1]; i += 1
        elif a.startswith("--long-file="):
            long_file = a.split("=", 1)[1]; i += 1
        else:
            print("forge-describe: unexpected argument " + repr(a) + "\\n" + USAGE, file=sys.stderr)
            sys.exit(2)
    if long_file is not None:
        if "description" in payload:
            print("forge-describe: pass --long or --long-file, not both", file=sys.stderr)
            sys.exit(2)
        try:
            if long_file == "-":
                payload["description"] = sys.stdin.read()
            else:
                with open(os.path.expanduser(long_file), encoding="utf-8") as handle:
                    payload["description"] = handle.read()
        except OSError as e:
            print("forge-describe: cannot read", long_file + ":", e, file=sys.stderr)
            sys.exit(2)
    if not payload:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    url = os.environ.get("FORGE_DESCRIBE_URL")
    if not url:
        print("forge-describe: not available in this session", file=sys.stderr)
        sys.exit(3)
    headers = {"content-type": "application/json"}
    token_file = os.environ.get("FORGE_BROKER_TOKEN_FILE")
    if token_file:
        # The broker's per-start loopback secret; read from the 0600 file, never from argv/env.
        try:
            with open(token_file, encoding="utf-8") as handle:
                headers["authorization"] = "Bearer " + handle.read().strip()
        except OSError as e:
            print("forge-describe: cannot read the broker token:", e, file=sys.stderr)
            sys.exit(3)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.stderr.write(e.read().decode()); sys.stderr.write("\\n"); sys.exit(1)
    except Exception as e:  # noqa: BLE001 - surface any transport error to the agent
        print("forge-describe:", e, file=sys.stderr); sys.exit(1)
    text = "".join(
        part.get("text", "") for part in result.get("content") or [] if isinstance(part, dict)
    )
    if result.get("isError"):
        print("forge-describe:", text, file=sys.stderr); sys.exit(1)
    stored = result.get("structuredContent")
    sys.stdout.write(json.dumps(stored, indent=2) if stored is not None else text)
    sys.stdout.write("\\n")


main()
'''


def _install_executable(bin_dir: Path, name: str, script: str) -> None:
    target = bin_dir / name
    target.write_text(script, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_forge_describe_shim(bin_dir: Path) -> None:
    """Write the engine-agnostic ``forge-describe`` PATH shim into ``bin_dir``."""
    _install_executable(bin_dir, "forge-describe", _FORGE_DESCRIBE_SHIM)


def ensure_codex_tool_shims(
    workspace_dir: str,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
    session_env: Mapping[str, str] | None = None,
) -> tuple[Path | None, dict[str, str]]:
    """Install the workspace PATH shims and return the env the agent process needs.

    ``session_env`` (the broker's :class:`SessionTools` env: present-file URL and
    loopback token file) is merged in, so every transport that installs the shims
    also makes them usable.
    """
    servers = list(mcp_servers or [])
    mount = _extract_mimir_mount(servers)
    workspace = Path(workspace_dir).expanduser().resolve()
    bin_dir = workspace / ".skuld-tools" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv_cache_dir = workspace / ".skuld-tools" / ".uv-cache"
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parents[2]
    ravn_config = os.environ.get("RAVN_CONFIG", "").strip()
    mimir_configured = mount is not None or _ravn_config_has_mimir(ravn_config)
    # Engine-agnostic present-file / forge-describe commands (claude AND codex sessions).
    _install_present_file_shim(bin_dir)
    _install_forge_describe_shim(bin_dir)

    commands: dict[str, tuple[str, str, str]] = {
        "tracker_issue": ("ravn.cli.tracker_bridge", "", "Search, read and update tracker issues."),
    }
    if mimir_configured:
        commands.update(
            {
                "mimir_ingest": (
                    "ravn.cli.mimir_bridge",
                    "ingest",
                    "Ingest a raw document into Mimir (assigns a source_id).",
                ),
                "mimir_search": (
                    "ravn.cli.mimir_bridge",
                    "search",
                    "Full-text search across Mimir wiki pages.",
                ),
                "mimir_read": (
                    "ravn.cli.mimir_bridge",
                    "read",
                    "Read a Mimir wiki page by path.",
                ),
                "mimir_read_source": (
                    "ravn.cli.mimir_bridge",
                    "read-source",
                    "Read a raw ingested Mimir source by source_id.",
                ),
                "mimir_write": (
                    "ravn.cli.mimir_bridge",
                    "write",
                    "Create or update a Mimir wiki page.",
                ),
                "mimir_publish_artifact": (
                    "ravn.cli.mimir_bridge",
                    "publish-artifact",
                    "Publish a JSON capability artifact to the durable artifact store.",
                ),
                "mimir_publish_files": (
                    "ravn.cli.mimir_bridge",
                    "publish-files",
                    "Publish workspace files into Mimir as wiki pages.",
                ),
                "mimir_list": (
                    "ravn.cli.mimir_bridge",
                    "list",
                    "List Mimir wiki page paths, optionally under a prefix.",
                ),
                "mimir_related": (
                    "ravn.cli.mimir_bridge",
                    "related",
                    "Traverse the Mimir wikilink graph from a page and return related "
                    "pages up to N hops away (--path <wiki path>, --depth 1-3, "
                    "optional --rel works_at to filter by typed relationship). "
                    "Use for relationship questions instead of keyword search.",
                ),
            }
        )
    for command_name, (module_name, subcommand, description) in commands.items():
        env_parts = [
            f'PYTHONPATH="{source_root}{os.pathsep}$PYTHONPATH"',
            f'UV_CACHE_DIR="{uv_cache_dir}"',
        ]
        command = f"exec env {' '.join(env_parts)} {shlex.quote(sys.executable)} -m {module_name}"
        if subcommand:
            command += f" {subcommand}"
        command += ' "$@"'
        target = bin_dir / command_name
        target.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    f"# {command_name}: {description}",
                    command,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}" + os.environ.get("PATH", ""),
        "RAVN_WORKSPACE_DIR": str(workspace),
        "UV_CACHE_DIR": str(uv_cache_dir),
    }
    if ravn_config:
        env["RAVN_CONFIG"] = ravn_config
    if mount is not None:
        mimir_path, mimir_name = mount
        env["RAVN_MIMIR_PATH"] = mimir_path
        if mimir_name:
            env["RAVN_MIMIR_NAME"] = mimir_name
    env.update(session_env or {})
    return bin_dir, env
