"""Shared page-collection helper for Mimir-backed resident adapters."""

from __future__ import annotations

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
    ``None``) are skipped. With ``limit`` set, collection stops once that many
    items have been parsed.
    """
    pages = sorted(
        await mimir.list_pages(prefix=prefix),
        key=lambda page: getattr(page, "path", ""),
        reverse=reverse,
    )
    items: list[T] = []
    for meta in pages:
        path = str(getattr(meta, "path", "") or "")
        if not path:
            continue
        try:
            content = await mimir.read_page(path)
        except FileNotFoundError:
            continue
        parsed = parse(content)
        if parsed is None:
            continue
        items.append(parsed)
        if limit is not None and len(items) >= limit:
            break
    return items
