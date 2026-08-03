"""Append-only raw observation archive for the resident inbox.

Every observation the resident receives is appended here before any queue work
happens, and nothing ever deletes from it.  The archive is date-partitioned
NDJSON: one append per observation, no directory growth, no parse on the write
path, and plain text that stays greppable for research.

The queue upstream of this is *coalescing* — many observations can share one
pending slot.  That is only safe because this archive keeps every one of them,
so a slot's aggregate is a summary of durable records rather than a replacement
for them.

References are ``YYYY-MM-DD:<byte offset>``.  They sort monotonically in arrival
order and address the exact line, so a slot can name the precise range of raw
records it covers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX = "resident/inbox/raw"

#: Sorts before every real reference, for callers with nothing acknowledged yet.
EMPTY_ARCHIVE_REF = ""


def archive_ref_sort_key(ref: str) -> tuple[str, int]:
    """Order references by arrival.

    Malformed references sort *before* every real one, deliberately.  The key
    decides how much of a slot a caller has already judged, and treating an
    unreadable reference as "judged nothing" leaves observations pending.  The
    opposite default would acknowledge observations the resident never saw.
    """
    date, _, offset = str(ref or "").partition(":")
    try:
        return (date, int(offset))
    except ValueError:
        return ("", -1)


class RawSignalArchive:
    """Date-partitioned append-only NDJSON log of raw observations."""

    def __init__(self, root: Path | str, *, prefix: str = ARCHIVE_PREFIX) -> None:
        self._root = Path(root).expanduser().resolve()
        self._prefix = prefix.strip("/").strip() or ARCHIVE_PREFIX

    @property
    def directory(self) -> Path:
        return self._root / self._prefix

    def append(self, record: dict[str, Any], *, arrived_at: datetime | None = None) -> str:
        """Append one record and return its reference.

        Partitioning uses *arrival* time, not the observation's own timestamp: a
        late-arriving observation must still append at the end of the log, or
        references would stop being monotonic.
        """
        moment = arrived_at or datetime.now(UTC)
        path = self.directory / f"{moment.strftime('%Y-%m-%d')}.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"archived_at": moment.isoformat(), **record},
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        )
        offset = path.stat().st_size if path.exists() else 0
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return f"{moment.strftime('%Y-%m-%d')}:{offset}"

    def read(self, ref: str) -> dict[str, Any] | None:
        """Return the archived record at ``ref``, or None when unreadable."""
        date, _, offset = str(ref or "").partition(":")
        if not date or not offset.isdigit():
            return None
        path = self.directory / f"{date}.ndjson"
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(int(offset))
                line = handle.readline()
        except OSError:
            logger.warning("resident inbox archive: unreadable reference %s", ref, exc_info=True)
            return None
        if not line.strip():
            return None
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("resident inbox archive: malformed record at %s", ref)
            return None
        return parsed if isinstance(parsed, dict) else None

    def read_range(
        self,
        *,
        after: str,
        through: str,
        limit: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return records with ``after < ref <= through``, oldest first.

        Used when a slot advanced while the resident was judging it: the delta
        is rebuilt from durable records rather than guessed from the aggregate.
        """
        if limit <= 0:
            return []
        low = archive_ref_sort_key(after) if after else ("", -1)
        high = archive_ref_sort_key(through)
        directory = self.directory
        if not directory.is_dir():
            return []
        collected: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(directory.glob("*.ndjson")):
            date = path.stem
            if date < low[0] or date > high[0]:
                continue
            offset = 0
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        ref = f"{date}:{offset}"
                        offset += len(line.encode("utf-8"))
                        key = archive_ref_sort_key(ref)
                        if key <= low or key > high:
                            continue
                        try:
                            parsed = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("resident inbox archive: malformed record at %s", ref)
                            continue
                        if isinstance(parsed, dict):
                            collected.append((ref, parsed))
                        if len(collected) >= limit:
                            return collected
            except OSError:
                logger.warning(
                    "resident inbox archive: unreadable partition %s", path, exc_info=True
                )
                continue
        return collected

    def count(self) -> int:
        """Total archived records. Reconciliation only — never on the hot path."""
        directory = self.directory
        if not directory.is_dir():
            return 0
        total = 0
        for path in sorted(directory.glob("*.ndjson")):
            with path.open("r", encoding="utf-8") as handle:
                total += sum(1 for line in handle if line.strip())
        return total
