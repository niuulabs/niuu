"""LogBasedUsageAdapter — MimirUsagePort backed by log.md query entries.

Derives access frequency by parsing ``wiki/log.md`` for query log lines.
Each ``## [date] query | ...`` line implies a read access to the pages that
answered the query.  This is a best-effort heuristic: it counts query events,
not individual page reads.

No extra storage is required beyond the existing log file.

Future alternatives: ``RedisUsageAdapter``, ``PostgresUsageAdapter``.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

from mimir.ports.usage import MimirUsagePort

logger = logging.getLogger(__name__)

_QUERY_LOG_RE = re.compile(r"^## \[[\d-]+\] query \| (.+)$")


class LogBasedUsageAdapter(MimirUsagePort):
    """Derives page-access frequency from log.md query entries.

    Args:
        mimir_root: Root directory of the Mímir knowledge base
                    (the directory that contains ``wiki/`` and ``raw/``).
    """

    def __init__(self, mimir_root: str | Path) -> None:
        self._root = Path(mimir_root).expanduser()
        self._log = self._root / "wiki" / "log.md"
        # In-memory access log for the current session (record_access calls)
        self._session_counts: Counter[str] = Counter()

    async def record_access(self, path: str) -> None:
        """Record a page access in the in-memory session counter."""
        self._session_counts[path] += 1

    async def top_pages(self, n: int = 20) -> list[tuple[str, int]]:
        """Return the *n* most-accessed pages, combining log.md and session counts."""
        counts = self._parse_log_counts()
        counts.update(self._session_counts)
        return counts.most_common(n)

    async def pages_above_threshold(self, min_accesses: int) -> list[str]:
        """Return page paths accessed at least *min_accesses* times."""
        counts = self._parse_log_counts()
        counts.update(self._session_counts)
        return [path for path, count in counts.items() if count >= min_accesses]

    def _parse_log_counts(self) -> Counter[str]:
        """Parse wiki/log.md and count query appearances per page path.

        Each query log entry is counted as one access to the pages it returned.
        Page paths are extracted from markdown links ``[text](path)`` in the
        log body following a query entry.
        """
        if not self._log.exists():
            return Counter()

        counts: Counter[str] = Counter()
        try:
            content = self._log.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("LogBasedUsageAdapter: could not read log.md: %s", exc)
            return Counter()

        in_query_entry = False
        for line in content.splitlines():
            if _QUERY_LOG_RE.match(line):
                in_query_entry = True
                continue
            if line.startswith("## "):
                in_query_entry = False
                continue
            if in_query_entry:
                # Extract wiki page paths from markdown links
                for path in re.findall(r"\[.*?\]\(([^)]+\.md)\)", line):
                    counts[path] += 1

        return counts
