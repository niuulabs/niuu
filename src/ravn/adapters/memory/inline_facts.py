"""Inline fact detection — regex patterns and Mímir compiled-truth page writer.

Replaces the mid-session auto-detection previously handled by the removed Búri
memory adapter.  Patterns match explicit preference / directive / decision /
retraction statements and write compact compiled-truth pages to Mímir.

The page format follows the NIU-573 compiled-truth + timeline convention:
  - YAML frontmatter with ``type`` and ``valid_from`` (or ``valid_until`` for
    retractions)
  - Markdown body starting with a ``# Title`` heading
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled patterns — identical to what Búri used for inline detection
# ---------------------------------------------------------------------------

_REMEMBER_PAT = re.compile(
    r"\b(remember\s+that|note\s+that|don['\u2019]t\s+forget)\b",
    re.IGNORECASE,
)
_PREFER_PAT = re.compile(
    r"\b(i\s+prefer|i\s+like|i\s+don['\u2019]t\s+like|i\s+hate|i\s+love)\b",
    re.IGNORECASE,
)
_DECISION_PAT = re.compile(
    r"\b(we\s+decided|let['\u2019]s\s+go\s+with|we['\u2019]re\s+going\s+with"
    r"|we\s+chose|we\s+picked)\b",
    re.IGNORECASE,
)
_FORGET_PAT = re.compile(
    r"\b(forget\s+that|actually\s+no|ignore\s+what\s+i\s+said|scratch\s+that)\b",
    re.IGNORECASE,
)

# Slug sanitiser — collapse non-alphanumeric runs to hyphens
_SLUG_PAT = re.compile(r"[^a-z0-9]+")

# Mimir category paths by fact type
_FACT_PATHS: dict[str, str] = {
    "directive": "memory/directives",
    "preference": "memory/preferences",
    "decision": "memory/decisions",
}

# Maximum number of leading words used to derive a page slug
_SLUG_WORD_LIMIT = 8

# Maximum slug length in characters (before .md extension)
_SLUG_MAX_CHARS = 48


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def detect_fact_type(text: str) -> str | None:
    """Return the fact type string if *text* matches an inline detection pattern.

    Returns one of ``"decision"``, ``"preference"``, or ``"directive"``,
    or ``None`` if no pattern matched.
    """
    if _DECISION_PAT.search(text):
        return "decision"
    if _PREFER_PAT.search(text):
        return "preference"
    if _REMEMBER_PAT.search(text):
        return "directive"
    return None


def is_retraction(text: str) -> bool:
    """Return ``True`` if *text* matches a retraction / forget pattern."""
    return bool(_FORGET_PAT.search(text))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Derive a filesystem-safe slug from the first words of *text*."""
    words = text.lower().split()[:_SLUG_WORD_LIMIT]
    raw = _SLUG_PAT.sub("-", " ".join(words)).strip("-")
    return raw[:_SLUG_MAX_CHARS]


def _build_fact_page(fact_type: str, content: str, valid_from: datetime) -> str:
    """Build a compiled-truth Mímir page in NIU-573 format."""
    iso = valid_from.strftime("%Y-%m-%dT%H:%M:%SZ")
    date = valid_from.strftime("%Y-%m-%d")
    return (
        "---\n"
        f"type: {fact_type}\n"
        f"valid_from: {iso}\n"
        "---\n"
        f"# {content.strip()}\n\n"
        f"**Recorded:** {date}\n\n"
        f"{content.strip()}\n"
    )


def _build_retraction_page(content: str, retracted_at: datetime) -> str:
    """Build a Mímir page that marks a previous statement as retracted."""
    iso = retracted_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    date = retracted_at.strftime("%Y-%m-%d")
    return (
        "---\n"
        "type: retracted\n"
        f"valid_until: {iso}\n"
        "---\n"
        f"# {content.strip()}\n\n"
        f"**Retracted:** {date}\n\n"
        f"~~{content.strip()}~~\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def detect_and_write(
    user_input: str,
    mimir: MimirPort,
) -> None:
    """Detect inline fact patterns in *user_input* and write Mímir pages.

    Called at the start of each agent turn.  Does nothing if no pattern
    matches.  Errors are logged and swallowed — fact detection must never
    interrupt a normal conversation turn.

    Retraction patterns ("forget that", "scratch that") write a page under
    ``memory/retractions/`` with ``type: retracted`` frontmatter.
    Other patterns write pages under ``memory/{preferences,directives,decisions}/``.
    """
    text = user_input.strip()
    now = datetime.now(UTC)

    if is_retraction(text):
        path = f"memory/retractions/{_slug(text)}.md"
        page = _build_retraction_page(text, retracted_at=now)
        try:
            await mimir.upsert_page(path, page)
            logger.debug("inline retraction written: %s", path)
        except Exception:
            logger.warning("inline retraction write failed", exc_info=True)
        return

    fact_type = detect_fact_type(text)
    if fact_type is None:
        return

    category = _FACT_PATHS[fact_type]
    path = f"{category}/{_slug(text)}.md"
    page = _build_fact_page(fact_type, text, valid_from=now)
    try:
        await mimir.upsert_page(path, page)
        logger.debug("inline fact written (%s): %s", fact_type, path)
    except Exception:
        logger.warning("inline fact write failed", exc_info=True)
