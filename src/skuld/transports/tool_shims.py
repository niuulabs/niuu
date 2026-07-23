"""Helpers for exposing workflow-specific shell shims to CLI transports."""

from __future__ import annotations

import os
import shutil
import stat
import textwrap
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


def _extract_platform_config() -> tuple[str, float, str]:
    base_url = "http://localhost:8080"
    timeout = 30.0
    pat_token = ""
    config_path = os.environ.get("RAVN_CONFIG", "").strip()
    if not config_path:
        return base_url, timeout, pat_token
    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return base_url, timeout, pat_token
    platform = (payload.get("gateway") or {}).get("platform") or {}
    if isinstance(platform.get("base_url"), str) and platform["base_url"].strip():
        base_url = platform["base_url"].strip()
    if isinstance(platform.get("timeout"), (int, float)):
        timeout = float(platform["timeout"])
    if isinstance(platform.get("pat_token"), str):
        pat_token = platform["pat_token"]
    return base_url, timeout, pat_token


def _ravn_config_has_mimir(config_path: str) -> bool:
    if not config_path:
        return False
    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    mimir = payload.get("mimir")
    return isinstance(mimir, dict) and any(bool(value) for value in mimir.values())


def _tracker_issue_script(base_url: str, timeout: float, pat_token: str) -> str:
    auth_header = f"Bearer {pat_token}" if pat_token else ""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import sys
        import urllib.error
        import urllib.parse
        import urllib.request

        BASE_URL = {base_url!r}.rstrip("/")
        TIMEOUT = {timeout!r}
        AUTH_HEADER = {auth_header!r}
        TRACKER_PATH = "/api/v1/tracker/issues"
        USAGE = "usage: tracker_issue <search|get|update-status|update_status> ..."

        def _request(method, path, params=None, body=None):
            url = BASE_URL + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            data = None
            headers = {{"Accept": "application/json"}}
            if AUTH_HEADER:
                headers["Authorization"] = AUTH_HEADER
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))

        def main(argv):
            if len(argv) < 2 or argv[1] in {{"-h", "--help", "help"}}:
                print(USAGE)
                return 0
            command = argv[1]
            try:
                if command == "search" and len(argv) == 3:
                    payload = _request("GET", TRACKER_PATH, params={{"q": argv[2]}})
                elif command == "get" and len(argv) == 3:
                    payload = _request("GET", f"{{TRACKER_PATH}}/{{argv[2]}}")
                elif command in {{"update-status", "update_status"}} and len(argv) == 4:
                    payload = _request(
                        "PATCH",
                        f"{{TRACKER_PATH}}/{{argv[2]}}",
                        body={{"status": argv[3]}},
                    )
                elif command == "search":
                    print("usage: tracker_issue search <query>")
                    return 1
                elif command == "get":
                    print("usage: tracker_issue get <issue-id>")
                    return 1
                elif command in {{"update-status", "update_status"}}:
                    print("usage: tracker_issue update-status <issue-id> <status>")
                    return 1
                else:
                    raise SystemExit(
                        f"unsupported tracker_issue invocation: {{' '.join(argv[1:])}}"
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {{"error": raw or str(exc)}}
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 1
            except Exception as exc:
                print(json.dumps({{"error": str(exc)}}, indent=2, sort_keys=True))
                return 1
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main(sys.argv))
        """
    )


# The `present-file` command — an ENGINE-AGNOSTIC (claude + codex) PATH shim that hands the user a
# file in the Lexi app (in-app SendUserFile). It POSTs the resolved absolute path to the broker's
# loopback endpoint (URL from FORGE_PRESENT_FILE_URL, injected by the transport); the broker stages
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
    payload = {"path": os.path.abspath(os.path.expanduser(path))}
    if caption:
        payload["caption"] = caption
    if title:
        payload["title"] = title
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST",
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
    target = bin_dir / "present-file"
    target.write_text(_PRESENT_FILE_SHIM, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_codex_tool_shims(
    workspace_dir: str,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> tuple[Path | None, dict[str, str]]:
    servers = list(mcp_servers or [])
    mount = _extract_mimir_mount(servers)
    workspace = Path(workspace_dir).expanduser().resolve()
    bin_dir = workspace / ".skuld-tools" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv_cache_dir = workspace / ".skuld-tools" / ".uv-cache"
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parents[2]
    project_root = source_root.parent
    uv_bin = shutil.which("uv") or "uv"
    ravn_config = os.environ.get("RAVN_CONFIG", "").strip()
    mimir_configured = mount is not None or _ravn_config_has_mimir(ravn_config)
    platform_base_url, platform_timeout, platform_pat = _extract_platform_config()

    tracker_target = bin_dir / "tracker_issue"
    tracker_target.write_text(
        _tracker_issue_script(platform_base_url, platform_timeout, platform_pat),
        encoding="utf-8",
    )
    tracker_target.chmod(tracker_target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Engine-agnostic present-file command (available to claude AND codex tmux sessions).
    _install_present_file_shim(bin_dir)

    commands: dict[str, tuple[str, str, str]] = {}
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
        if ravn_config:
            env_parts.append(f'RAVN_CONFIG="{ravn_config}"')
        command = (
            f'exec env {" ".join(env_parts)} "{uv_bin}" run --project '
            f'"{project_root}" python -m {module_name}'
        )
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
    return bin_dir, env
