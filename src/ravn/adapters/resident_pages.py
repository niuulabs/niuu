"""Shared page-collection helper for Mimir-backed resident adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

from ravn.ports.mimir import MimirPort

T = TypeVar("T")


async def collect_pages(
    mimir: MimirPort,
    prefix: str,
    parse: Callable[[str], T | None],
    *,
    reverse: bool = False,
    limit: int | None = None,
) -> list[T]:
    """List pages under ``prefix``, read each in path order, and parse them.

    Pages that are missing (raced deletion) or that ``parse`` rejects (returns
    ``None``) are skipped. With ``limit`` set, reads run sequentially and stop
    once that many items have been parsed; without a limit every page is needed,
    so the reads run concurrently.
    """
    pages = sorted(
        await mimir.list_pages(prefix=prefix),
        key=lambda page: getattr(page, "path", ""),
        reverse=reverse,
    )
    paths = [path for meta in pages if (path := str(getattr(meta, "path", "") or ""))]
    if limit is None:
        return _parse_contents(
            await asyncio.gather(
                *(mimir.read_page(path) for path in paths),
                return_exceptions=True,
            ),
            parse,
        )
    items: list[T] = []
    for path in paths:
        try:
            content = await mimir.read_page(path)
        except FileNotFoundError:
            continue
        parsed = parse(content)
        if parsed is None:
            continue
        items.append(parsed)
        if len(items) >= limit:
            break
    return items


def _parse_contents(
    results: list[str | BaseException],
    parse: Callable[[str], T | None],
) -> list[T]:
    items: list[T] = []
    for content in results:
        if isinstance(content, FileNotFoundError):
            continue
        if isinstance(content, BaseException):
            raise content
        parsed = parse(content)
        if parsed is not None:
            items.append(parsed)
    return items
