"""Merging Forge notification feeds across Guild instances (contract §6).

Each Forge node orders its own feed by a node-local ``seq``. A fleet-wide page
merges the per-node pages newest first by ``(created_at, instance_id, seq)`` and
carries one position per node in an opaque cursor: base64url JSON
``{instance_id: seq}``. A ``null`` position means "from that node's newest"; a node
absent from a ``before`` cursor has no older notifications left, and a node absent
from an ``after`` cursor is not gap-filled (send it with seq 0 to read from the start).
"""

from __future__ import annotations

import base64
import binascii
import heapq
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class CursorError(ValueError):
    """The opaque cursor is not a base64url JSON ``{instance_id: seq}`` object."""


def encode_cursor(positions: Mapping[str, int | None]) -> str:
    raw = json.dumps(dict(sorted(positions.items())), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> dict[str, int | None]:
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise CursorError("Cursor must be base64url JSON {instance_id: seq}") from exc
    if not isinstance(decoded, dict):
        raise CursorError("Cursor must be base64url JSON {instance_id: seq}")
    positions: dict[str, int | None] = {}
    for instance_id, seq in decoded.items():
        valid_seq = seq is None or (type(seq) is int and seq >= 0)
        if not instance_id or not valid_seq:
            raise CursorError("Cursor positions must be non-negative integers or null")
        positions[instance_id] = seq
    return positions


def _timestamp(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _seq(item: dict[str, Any]) -> int:
    seq = item.get("seq")
    return seq if isinstance(seq, int) else 0


def _merge_key(item: dict[str, Any]) -> tuple[float, str, int]:
    return (_timestamp(item.get("created_at")), str(item.get("instance_id") or ""), _seq(item))


@dataclass
class InstancePage:
    """One node's page, already tagged with its ``instance_id``."""

    items: list[dict[str, Any]]
    next_before: int | None
    head_seq: int
    read_through_seq: int
    unread_count: int
    revision: int | None = None


@dataclass
class MergedPage:
    items: list[dict[str, Any]]
    next_before: dict[str, int | None] | None
    next_after: dict[str, int]
    summaries: dict[str, dict[str, int]] = field(default_factory=dict)


def _summaries(pages: Mapping[str, InstancePage]) -> dict[str, dict[str, int]]:
    return {
        instance_id: {
            "head_seq": page.head_seq,
            "read_through_seq": page.read_through_seq,
            "unread_count": page.unread_count,
            **({"revision": page.revision} if page.revision is not None else {}),
        }
        for instance_id, page in pages.items()
    }


def merge_newest(
    pages: Mapping[str, InstancePage],
    *,
    previous: Mapping[str, int | None],
    unavailable: set[str],
    limit: int | None,
) -> MergedPage:
    """Merge newest-first pages and compute the next ``before`` cursor.

    ``previous`` holds each node's position for this request (``None`` = newest).
    ``limit`` bounds the merged page; without one, a node that reported more
    rows returned a full page, so the smallest such page length is the page size.
    Unavailable nodes keep their previous position so the next page retries them.
    """
    # A k-way merge of each node's seq-ordered page: rows taken from one node are
    # always a prefix of its page, so its cursor can never skip a row, even when
    # clocks disagree with seq order.
    merged = list(
        heapq.merge(
            *(sorted(page.items, key=_seq, reverse=True) for page in pages.values()),
            key=_merge_key,
            reverse=True,
        )
    )
    if limit is None:
        full_pages = [len(page.items) for page in pages.values() if page.next_before is not None]
        limit = min(full_pages) if full_pages else len(merged)
    kept = merged[:limit]
    positions: dict[str, int | None] = {}
    for instance_id, page in pages.items():
        included = [item["seq"] for item in kept if item.get("instance_id") == instance_id]
        more = page.next_before is not None or len(included) < len(page.items)
        if not more:
            continue
        positions[instance_id] = min(included) if included else previous.get(instance_id)
    for instance_id in unavailable:
        positions[instance_id] = previous.get(instance_id)
    return MergedPage(
        items=kept,
        next_before=positions or None,
        next_after={instance_id: page.head_seq for instance_id, page in pages.items()},
        summaries=_summaries(pages),
    )


def merge_after(
    pages: Mapping[str, InstancePage],
    *,
    previous: Mapping[str, int | None],
    unavailable: set[str],
) -> MergedPage:
    """Merge ascending gap-fill pages; every returned row is kept.

    Each node bounded its own page, so nothing is cut here. The next ``after``
    cursor is the highest seq returned per node (or its previous position).
    """
    merged = list(
        heapq.merge(*(sorted(page.items, key=_seq) for page in pages.values()), key=_merge_key)
    )
    positions: dict[str, int] = {}
    for instance_id, page in pages.items():
        seqs = [item["seq"] for item in page.items if isinstance(item.get("seq"), int)]
        positions[instance_id] = max(seqs) if seqs else previous.get(instance_id) or 0
    for instance_id in unavailable:
        positions[instance_id] = previous.get(instance_id) or 0
    return MergedPage(
        items=merged,
        next_before=None,
        next_after=positions,
        summaries=_summaries(pages),
    )
