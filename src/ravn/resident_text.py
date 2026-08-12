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

#: Words carried by almost any sentence, so they say nothing about whether two
#: texts mean the same thing. Callers add their own domain filler on top.
_BASE_STOPWORDS = frozenset(
    {"a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "of", "is"}
)

#: Below this many significant words the overlap coefficient is trivially high
#: (a two-word text is "contained" in almost anything), so require near-identity
#: instead.
MIN_OVERLAP_WORDS = 5


def significant_words(text: str, *, extra_stopwords: frozenset[str] = frozenset()) -> set[str]:
    """Return the meaning-bearing word set of *text*, lowercased.

    Words shorter than three characters and the stopword sets are dropped, so
    two texts that differ only in phrasing reduce to the same set.
    """
    stop = _BASE_STOPWORDS | extra_stopwords
    return {w for w in re.findall(r"\b\w{3,}\b", text.lower()) if w not in stop}


def texts_similar(
    a: str,
    b: str,
    *,
    threshold: float,
    extra_stopwords: frozenset[str] = frozenset(),
) -> bool:
    """Whether *a* and *b* share enough significant words (Jaccard) to be duplicates.

    Jaccard punishes a paraphrase for the words it did not reuse, which is the
    right question for short labels where both sides are the same kind of thing.
    Use :func:`texts_overlap` when one text may be much longer than the other.
    """
    words_a = significant_words(a, extra_stopwords=extra_stopwords)
    words_b = significant_words(b, extra_stopwords=extra_stopwords)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / len(words_a | words_b) >= threshold


def texts_overlap(
    a: str,
    b: str,
    *,
    threshold: float,
    extra_stopwords: frozenset[str] = frozenset(),
) -> bool:
    """Whether the shorter of two texts is largely contained in the longer one.

    The overlap coefficient asks the question that matters when one text
    elaborates on another ("is the shorter one contained in the longer?"),
    where Jaccard would penalise the elaboration for its extra words.
    """
    words_a = significant_words(a, extra_stopwords=extra_stopwords)
    words_b = significant_words(b, extra_stopwords=extra_stopwords)
    if not words_a or not words_b:
        return False
    shorter = min(len(words_a), len(words_b))
    if shorter < MIN_OVERLAP_WORDS:
        return words_a == words_b
    return len(words_a & words_b) / shorter >= threshold


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
