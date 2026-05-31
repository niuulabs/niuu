"""Shell-friendly bridge for Mimir operations in CLI-driven runtimes.

This module exposes a tiny command surface so transports like Codex subprocess
can access the same durable Mimir operations the personas talk about, without
requiring first-class MCP tool support inside the model runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.tools.mimir_tools import (
    MimirIngestTool,
    MimirPublishFilesTool,
    MimirReadSourceTool,
    MimirReadTool,
    MimirSearchTool,
    MimirWriteTool,
)
from ravn.domain.models import ToolResult


def _workspace() -> Path:
    raw = os.environ.get("RAVN_WORKSPACE_DIR", "").strip()
    if not raw:
        return Path.cwd()
    return Path(raw).expanduser().resolve()


def _mimir_root() -> Path:
    raw = os.environ.get("RAVN_MIMIR_PATH", "").strip()
    if not raw:
        raise SystemExit("RAVN_MIMIR_PATH is not configured for this session")
    return Path(raw).expanduser().resolve()


def _default_mimir_name() -> str | None:
    raw = os.environ.get("RAVN_MIMIR_NAME", "").strip()
    return raw or None


def _adapter() -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=_mimir_root())


def _read_content(args: argparse.Namespace) -> tuple[str, str | None]:
    if getattr(args, "content", None):
        return str(args.content), getattr(args, "url", None)

    content_file = getattr(args, "content_file", "") or ""
    if content_file:
        file_path = Path(content_file).expanduser()
        if str(file_path) == "-":
            return sys.stdin.read(), getattr(args, "url", None)
        return file_path.read_text(encoding="utf-8", errors="replace"), getattr(args, "url", None)

    path = getattr(args, "path", "") or ""
    if path:
        if path == "-":
            return sys.stdin.read(), getattr(args, "url", None)
        file_path = Path(path).expanduser()
        if file_path.exists():
            return (
                file_path.read_text(encoding="utf-8", errors="replace"),
                getattr(args, "url", None),
            )

    url = getattr(args, "url", "") or ""
    if url:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        return response.text, url

    raise SystemExit(
        "No content provided. Supply a path, --content-file, --content, stdin, or --url."
    )


def _print_result(result: ToolResult) -> int:
    stream = sys.stderr if result.is_error else sys.stdout
    stream.write(str(result.content).rstrip() + "\n")
    return 1 if result.is_error else 0


async def _run_ingest(args: argparse.Namespace) -> int:
    content, origin_url = _read_content(args)
    title = str(args.title or "").strip()
    if not title:
        if getattr(args, "url", None):
            title = str(args.url).rstrip("/").rsplit("/", 1)[-1] or "web-source"
        elif getattr(args, "path", None) and str(args.path) not in {"", "-"}:
            title = Path(str(args.path)).stem.replace("-", " ").replace("_", " ").title()
        else:
            title = "stdin"
    tool = MimirIngestTool(_adapter())
    result = await tool.execute(
        {
            "content": content,
            "title": title,
            "source_type": args.source_type,
            "origin_url": origin_url,
            "mimir": args.mimir or _default_mimir_name(),
        }
    )
    return _print_result(result)


async def _run_search(args: argparse.Namespace) -> int:
    tool = MimirSearchTool(_adapter())
    result = await tool.execute({"query": args.query})
    return _print_result(result)


async def _run_read(args: argparse.Namespace) -> int:
    tool = MimirReadTool(_adapter())
    result = await tool.execute({"path": args.path})
    return _print_result(result)


async def _run_read_source(args: argparse.Namespace) -> int:
    tool = MimirReadSourceTool(_adapter())
    result = await tool.execute(
        {"source_id": args.source_id, "mimir": args.mimir or _default_mimir_name()}
    )
    return _print_result(result)


async def _run_write(args: argparse.Namespace) -> int:
    if args.content:
        content = str(args.content)
    elif args.content_file:
        file_path = Path(str(args.content_file)).expanduser()
        if str(file_path) == "-":
            content = sys.stdin.read()
        else:
            content = file_path.read_text(encoding="utf-8", errors="replace")
    else:
        content = sys.stdin.read()
    tool = MimirWriteTool(_adapter())
    result = await tool.execute(
        {
            "path": args.path,
            "content": content,
            "mimir": args.mimir or _default_mimir_name(),
        }
    )
    return _print_result(result)


async def _run_publish_files(args: argparse.Namespace) -> int:
    tool = MimirPublishFilesTool(_adapter(), workspace=_workspace())
    result = await tool.execute(
        {"paths": list(args.paths), "mimir": args.mimir or _default_mimir_name()}
    )
    return _print_result(result)


def _run_list(args: argparse.Namespace) -> int:
    root = _mimir_root() / "wiki"
    if not root.exists():
        print("wiki root not found", file=sys.stderr)
        return 1
    prefix = (args.prefix or "").strip().strip("/")
    base = root / prefix if prefix else root
    if not base.exists():
        print(f"no entries for prefix: {prefix}", file=sys.stderr)
        return 1
    for path in sorted(base.rglob("*.md")):
        print(path.relative_to(root).as_posix())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mimir-bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("path", nargs="?", default="")
    ingest.add_argument("--title", default="")
    ingest.add_argument("--type", dest="source_type", default="document")
    ingest.add_argument("--url", default="")
    ingest.add_argument("--content", default="")
    ingest.add_argument("--content-file", default="")
    ingest.add_argument("--mimir", default="")

    search = sub.add_parser("search")
    search.add_argument("query")

    read = sub.add_parser("read")
    read.add_argument("path")

    read_source = sub.add_parser("read-source")
    read_source.add_argument("source_id")
    read_source.add_argument("--mimir", default="")

    write = sub.add_parser("write")
    write.add_argument("path")
    write.add_argument("--content", default="")
    write.add_argument("--content-file", default="-")
    write.add_argument("--mimir", default="")

    publish_files = sub.add_parser("publish-files")
    publish_files.add_argument("paths", nargs="+")
    publish_files.add_argument("--mimir", default="")

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("prefix", nargs="?", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command == "ingest":
        return asyncio.run(_run_ingest(args))
    if command == "search":
        return asyncio.run(_run_search(args))
    if command == "read":
        return asyncio.run(_run_read(args))
    if command == "read-source":
        return asyncio.run(_run_read_source(args))
    if command == "write":
        return asyncio.run(_run_write(args))
    if command == "publish-files":
        return asyncio.run(_run_publish_files(args))
    if command == "list":
        return _run_list(args)
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
