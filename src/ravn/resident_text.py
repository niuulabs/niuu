"""Shared text and markdown helpers for the resident modules.

This is a leaf module (stdlib only) so any resident module or adapter can import
it without creating an import cycle. It holds the slug/compact-line/markdown
helpers that were previously copy-pasted across the resident family.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def slug(value: str, *, max_length: int = 80, fallback: str = "") -> str:
    """Lowercase, collapse non-alphanumeric runs to ``-``, trim, and truncate."""
    result = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")[:max_length]
    return result or fallback


def compact_line(text: str, *, limit: int = 240, marker: str = "…") -> str:
    """Collapse whitespace to single spaces and truncate with ``marker``."""
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + marker


def timestamp_slug(value: datetime) -> str:
    """Render a datetime as a sortable, path-safe stamp."""
    return value.strftime(_TIMESTAMP_FORMAT)


def merge_unique(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Concatenate two sequences, dropping duplicates while preserving order."""
    return tuple(dict.fromkeys((*left, *right)))


def append_unique(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    """Append ``item`` (stripped) to ``items`` unless empty or already present."""
    value = item.strip()
    if not value or value in items:
        return items
    return (*items, value)


def metadata(content: str) -> dict[str, str]:
    """Parse ``- key: value`` lines from a resident chronicle into a dict."""
    data: dict[str, str] = {}
    for line in content.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        data[key.strip()] = value.strip()
    return data


def section_lines(content: str, name: str) -> list[str]:
    """Return the non-blank lines under the ``## name`` heading."""
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            start = idx + 1
            break
    if start < 0:
        return []
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            collected.append(line)
    return collected


def section(content: str, name: str) -> str:
    """Return the prose (non list-item) text of the ``## name`` section."""
    lines = section_lines(content, name)
    return "\n".join(line for line in lines if not line.startswith("- ")).strip()


def section_items(content: str, name: str) -> list[str]:
    """Return the ``- item`` bullet values of the ``## name`` section."""
    items: list[str] = []
    for line in section_lines(content, name):
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value != "none":
                items.append(value)
    return items


def render_list(items: Any) -> str:
    """Render an iterable as a markdown bullet list, or ``- none`` when empty."""
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return "- none"
    return "\n".join(f"- {item}" for item in values)
