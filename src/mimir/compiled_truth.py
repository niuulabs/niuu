"""Compiled-truth page parser, validator, and mutation helpers.

This module implements the two-zone Mímir page format defined in FORMAT.md:

  - ``## Compiled Truth`` — rewritable synthesis zone
  - ``## Timeline``       — append-only evidence trail

The public API:

  parse_page(content)                     → CompiledTruthPage
  validate_page(content)                  → list[ValidationError]
  append_timeline_entry(content, entry)   → str
  rewrite_compiled_truth(content, truth)  → str
  extract_wikilinks(content)              → list[str]
  resolve_wikilink(slug, wiki_root)       → Path | None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from niuu.domain.mimir import (
    EntityType,
    PageConfidence,
    PageType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)

_COMPILED_TRUTH_HEADING = "## Compiled Truth"
_TIMELINE_HEADING = "## Timeline"

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# Page types that MUST have both zones (errors if absent)
_MANDATORY_ZONES: frozenset[PageType] = frozenset(
    {PageType.entity, PageType.directive, PageType.decision}
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TimelineEntry:
    """A single parsed timeline entry."""

    raw: str  # the full line including the leading "- "
    date: str  # YYYY-MM-DD
    description: str  # text before [Source:]
    source: str  # content inside [Source: ...]
    has_source: bool = True


@dataclass
class CompiledTruthPage:
    """Structured representation of a compiled-truth page."""

    frontmatter: dict
    compiled_truth: str  # body of the Compiled Truth zone (may be empty)
    timeline_entries: list[TimelineEntry]
    raw_content: str  # the original full content string
    # Parsed frontmatter fields (None if absent or invalid)
    page_type: PageType | None = None
    confidence: PageConfidence | None = None
    entity_type: EntityType | None = None
    related_entities: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationError:
    """A single structural compliance error."""

    code: str  # machine-readable error code
    message: str  # human-readable description


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_frontmatter(content: str) -> str:
    """Return *content* with the leading YAML frontmatter block removed."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    return content[match.end() :]


def _parse_frontmatter(content: str) -> dict:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _extract_zone(body: str, heading: str) -> str:
    """Return the text that follows *heading* up to the next ``##`` heading."""
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        return ""
    return match.group(1).rstrip()


def _parse_timeline_entries(timeline_body: str) -> list[TimelineEntry]:
    """Parse individual timeline entries from the timeline zone body."""
    entries: list[TimelineEntry] = []
    for line in timeline_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        text = stripped[2:]
        # Extract date
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2}): (.+)", text)
        if not date_match:
            entries.append(
                TimelineEntry(raw=stripped, date="", description=text, source="", has_source=False)
            )
            continue
        date = date_match.group(1)
        rest = date_match.group(2)
        source_match = re.search(r"\[Source: ([^\]]+)\]", rest)
        if source_match:
            source = source_match.group(1)
            description = rest[: source_match.start()].rstrip(". ")
            entries.append(
                TimelineEntry(raw=stripped, date=date, description=description, source=source)
            )
        else:
            entries.append(
                TimelineEntry(
                    raw=stripped, date=date, description=rest, source="", has_source=False
                )
            )
    return entries


def _parse_enum_field[E: StrEnum](raw: dict, key: str, enum_cls: type[E]) -> E | None:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_page(content: str) -> CompiledTruthPage:
    """Parse *content* into a :class:`CompiledTruthPage`.

    This function is non-strict: it returns whatever structure it can extract
    even if the page does not fully conform to the format.  Use
    :func:`validate_page` to check compliance.
    """
    raw_fm = _parse_frontmatter(content)
    body = _strip_frontmatter(content)
    compiled_truth = _extract_zone(body, _COMPILED_TRUTH_HEADING)
    timeline_body = _extract_zone(body, _TIMELINE_HEADING)
    timeline_entries = _parse_timeline_entries(timeline_body)

    page_type = _parse_enum_field(raw_fm, "type", PageType)
    confidence = _parse_enum_field(raw_fm, "confidence", PageConfidence)
    entity_type = _parse_enum_field(raw_fm, "entity_type", EntityType)

    related = raw_fm.get("related_entities") or []
    source_ids = raw_fm.get("source_ids") or []

    return CompiledTruthPage(
        frontmatter=raw_fm,
        compiled_truth=compiled_truth,
        timeline_entries=timeline_entries,
        raw_content=content,
        page_type=page_type,
        confidence=confidence,
        entity_type=entity_type,
        related_entities=list(related),
        source_ids=list(source_ids),
    )


def validate_page(content: str) -> list[ValidationError]:
    """Check *content* for structural compliance and return any errors found.

    Detected error codes:

    - ``MISSING_COMPILED_TRUTH``  — the ``## Compiled Truth`` section is absent
    - ``MISSING_TIMELINE``        — the ``## Timeline`` section is absent
    - ``TIMELINE_ENTRY_NO_SOURCE``— a timeline entry lacks ``[Source: ...]``
    - ``ENTITY_TYPE_MISMATCH``    — ``type: entity`` but ``entity_type`` absent
    """
    errors: list[ValidationError] = []
    page = parse_page(content)
    body = _strip_frontmatter(content)

    requires_zones = page.page_type in _MANDATORY_ZONES if page.page_type else False

    if requires_zones and _COMPILED_TRUTH_HEADING not in body:
        errors.append(
            ValidationError(
                code="MISSING_COMPILED_TRUTH",
                message=(
                    f"Pages of type '{page.page_type}' must contain a "
                    f"'{_COMPILED_TRUTH_HEADING}' section."
                ),
            )
        )

    if requires_zones and _TIMELINE_HEADING not in body:
        errors.append(
            ValidationError(
                code="MISSING_TIMELINE",
                message=(
                    f"Pages of type '{page.page_type}' must contain a "
                    f"'{_TIMELINE_HEADING}' section."
                ),
            )
        )

    for entry in page.timeline_entries:
        if not entry.has_source:
            errors.append(
                ValidationError(
                    code="TIMELINE_ENTRY_NO_SOURCE",
                    message=(f"Timeline entry is missing '[Source: ...]': {entry.raw!r}"),
                )
            )

    if page.page_type == PageType.entity and page.entity_type is None:
        errors.append(
            ValidationError(
                code="ENTITY_TYPE_MISMATCH",
                message="Pages with 'type: entity' must specify 'entity_type'.",
            )
        )

    return errors


def append_timeline_entry(content: str, entry: str) -> str:
    """Append *entry* to the ``## Timeline`` section of *content*.

    *entry* should be a single line in the format::

        - YYYY-MM-DD: Description. [Source: who, channel, date]

    If the Timeline section does not exist it is created at the end of the
    document.  Existing content is never modified.
    """
    entry_line = entry.rstrip("\n")

    if _TIMELINE_HEADING in content:
        # Find where the timeline section ends (next ## heading or EOF).
        pattern = re.compile(
            rf"({re.escape(_TIMELINE_HEADING)}\s*\n)(.*?)(\n(?=## )|\Z)",
            re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            existing_body = match.group(2).rstrip("\n")
            separator = "\n" if existing_body else ""
            new_body = f"{existing_body}{separator}\n{entry_line}"
            return content[: match.start(2)] + new_body + content[match.end(2) :]

        # Fallback: find the heading and append after it
        idx = content.index(_TIMELINE_HEADING) + len(_TIMELINE_HEADING)
        return content[:idx] + "\n\n" + entry_line + content[idx:]

    # Timeline section absent — append it
    trailer = "" if content.endswith("\n") else "\n"
    return f"{content}{trailer}\n{_TIMELINE_HEADING}\n\n{entry_line}\n"


def rewrite_compiled_truth(content: str, new_truth: str) -> str:
    """Replace the ``## Compiled Truth`` zone body with *new_truth*.

    The Timeline zone and all other content are preserved intact.

    If the Compiled Truth section is absent it is inserted before the Timeline
    section (or appended if neither exists).
    """
    new_truth_body = new_truth.rstrip("\n") + "\n"

    if _COMPILED_TRUTH_HEADING in content:
        pattern = re.compile(
            rf"({re.escape(_COMPILED_TRUTH_HEADING)}\s*\n)(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            return content[: match.start(2)] + new_truth_body + "\n" + content[match.end(2) :]

    # Section absent — insert before Timeline or append
    if _TIMELINE_HEADING in content:
        idx = content.index(_TIMELINE_HEADING)
        return (
            content[:idx] + _COMPILED_TRUTH_HEADING + "\n\n" + new_truth_body + "\n" + content[idx:]
        )

    trailer = "" if content.endswith("\n") else "\n"
    return f"{content}{trailer}\n{_COMPILED_TRUTH_HEADING}\n\n{new_truth_body}"


def extract_wikilinks(content: str) -> list[str]:
    """Return a deduplicated list of wikilink slugs found in *content*.

    ``[[person-karpathy]]`` → ``"person-karpathy"``

    Order is preserved (first occurrence wins for deduplication).
    """
    seen: set[str] = set()
    result: list[str] = []
    for match in _WIKILINK_RE.finditer(content):
        slug = match.group(1).strip()
        if slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result


def resolve_wikilink(slug: str, wiki_root: Path) -> Path | None:
    """Return the absolute path for *slug* if the entity file exists.

    Resolves ``[[slug]]`` → ``<wiki_root>/entities/<slug>.md``.
    Returns ``None`` if the file does not exist.
    """
    candidate = wiki_root / "entities" / f"{slug}.md"
    if candidate.exists():
        return candidate
    return None
