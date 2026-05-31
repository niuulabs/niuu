"""Shell-friendly bridge for tracker issue operations in CLI-driven runtimes.

This mirrors the tracker platform tool over a tiny CLI surface so Codex-backed
workflow sessions can use the shared tracker API without relying on first-class
tool plumbing inside the transport.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from ravn.config import Settings

_TRACKER_ISSUES_PATH = "/api/v1/tracker/issues"


def _settings() -> Settings:
    return Settings()


def _client() -> httpx.AsyncClient:
    settings = _settings()
    platform = settings.gateway.platform
    headers: dict[str, str] = {}
    if platform.pat_token:
        headers["Authorization"] = f"Bearer {platform.pat_token}"
    return httpx.AsyncClient(
        base_url=platform.base_url.rstrip("/"),
        timeout=platform.timeout,
        headers=headers,
    )


def _write_payload(payload: object, *, is_error: bool = False) -> int:
    stream = sys.stderr if is_error else sys.stdout
    stream.write(json.dumps(payload, indent=2, sort_keys=True, default=str))
    stream.write("\n")
    return 1 if is_error else 0


async def _run_search(args: argparse.Namespace) -> int:
    async with _client() as client:
        response = await client.get(_TRACKER_ISSUES_PATH, params={"q": args.query})
        response.raise_for_status()
        return _write_payload(response.json())


async def _run_get(args: argparse.Namespace) -> int:
    async with _client() as client:
        response = await client.get(f"{_TRACKER_ISSUES_PATH}/{args.issue_id}")
        response.raise_for_status()
        return _write_payload(response.json())


async def _run_update_status(args: argparse.Namespace) -> int:
    async with _client() as client:
        response = await client.patch(
            f"{_TRACKER_ISSUES_PATH}/{args.issue_id}",
            json={"status": args.status},
        )
        response.raise_for_status()
        return _write_payload(response.json())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracker_issue")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search")
    search.add_argument("query")

    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("issue_id")

    update = sub.add_parser("update-status")
    update.add_argument("issue_id")
    update.add_argument("status")

    update_alias = sub.add_parser("update_status")
    update_alias.add_argument("issue_id")
    update_alias.add_argument("status")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            return asyncio.run(_run_search(args))
        if args.command == "get":
            return asyncio.run(_run_get(args))
        if args.command in {"update-status", "update_status"}:
            return asyncio.run(_run_update_status(args))
        raise SystemExit(f"unknown command: {args.command}")
    except httpx.HTTPStatusError as exc:
        payload: object
        try:
            payload = exc.response.json()
        except Exception:
            payload = {"error": str(exc)}
        return _write_payload(payload, is_error=True)
    except Exception as exc:
        return _write_payload({"error": str(exc)}, is_error=True)


if __name__ == "__main__":
    raise SystemExit(main())
