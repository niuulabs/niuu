"""Timeline-zone date parsing shared between the Mímir service and Niuu's graph projection.

A Mímir page may carry an append-only ``## Timeline`` zone (see
``mimir/FORMAT.md``) whose entries look like::

    - 2026-01-02: Something happened. [Source: who, channel, date]

Both ``mimir.compiled_truth`` (full timeline entry parsing — description,
source, validation) and ``niuu.domain.knowledge_graph`` (dates only, to
compute a graph node's ``first_seen``) need to recognise a dated entry line
and find the zone it lives in. Niuu cannot import Mímir
(``.claude/rules/module-boundaries.md``), so this is the one place the
zone-extraction and dated-entry regexes live; ``mimir.compiled_truth``
imports them back rather than keeping its own copy.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

#: The heading Mímir pages use for their append-only evidence trail.
TIMELINE_HEADING = "## Timeline"

_DATED_ENTRY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}): (.+)")
_ZONE_RE_CACHE: dict[str, re.Pattern[str]] = {}


def extract_zone(body: str, heading: str) -> str:
    """Return the text following *heading* up to the next ``##`` heading, or "".

    Generic over the heading so both the Compiled Truth and Timeline zones
    (and any future ``##`` zone) share one extraction rule.
    """
    pattern = _ZONE_RE_CACHE.get(heading)
    if pattern is None:
        pattern = re.compile(
            rf"^{re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
        )
        _ZONE_RE_CACHE[heading] = pattern
    match = pattern.search(body)
    if not match:
        return ""
    return match.group(1).rstrip()


def parse_dated_entry(line: str) -> tuple[str, str] | None:
    """Return ``(date, rest)`` for a raw ``- YYYY-MM-DD: ...`` timeline line.

    *line* is a single line from a Timeline zone, still carrying its leading
    ``- `` marker and any surrounding whitespace. Returns ``None`` when the
    line is not a list entry, or has no ``YYYY-MM-DD:`` date prefix.
    """
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    match = _DATED_ENTRY_RE.match(stripped[2:])
    if not match:
        return None
    return match.group(1), match.group(2)


def extract_entry_dates(content: str) -> list[datetime]:
    """Return UTC midnight datetimes for every dated entry in *content*'s Timeline zone.

    *content* is a full page body (frontmatter may or may not be stripped —
    frontmatter YAML never contains a literal ``## Timeline`` line, so the
    zone regex is safe to run against it directly). Entries without a
    ``YYYY-MM-DD:`` prefix carry no date and are skipped; a date-only entry
    is treated as midnight UTC on that day, matching
    ``mimir.learning._parse_date``.
    """
    zone = extract_zone(content, TIMELINE_HEADING)
    dates: list[datetime] = []
    for line in zone.splitlines():
        parsed = parse_dated_entry(line)
        if parsed is None:
            continue
        date_str, _rest = parsed
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            # Shaped like a date but not one on the calendar (2026-02-30):
            # the line carries no date, exactly like one without the prefix.
            # Raising here would take the whole graph down for one typo.
            continue
        dates.append(day.replace(tzinfo=UTC))
    return dates
