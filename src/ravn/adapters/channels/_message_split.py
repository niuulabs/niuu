"""Split outbound chat text at a platform's message limit instead of cutting it.

A platform limit is a real bound, but it bounds one message, not what the
resident has to say. Cutting at it and appending "..." dropped the rest of the
reply — often the conclusion — with nothing on either side noticing.
"""

from __future__ import annotations


def split_message(text: str, limit: int) -> list[str]:
    """Return *text* as consecutive chunks of at most *limit* characters.

    Each chunk ends at the last line break before the limit, else at the last
    space, else exactly at the limit. Nothing is dropped except the whitespace
    a chunk was split on.
    """
    if limit <= 0:
        raise ValueError(f"message limit must be positive, got {limit}")
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n ") if cut < limit else rest[cut:]
    chunks.append(rest)
    return chunks
