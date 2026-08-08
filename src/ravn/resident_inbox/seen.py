"""Durable record of which observations a resident has already ingested.

A polling signal source has no "give me what is new" call: the Kubernetes
events API returns everything it still retains — roughly an hour of history —
on every request.  What stops each poll re-judging the same two hundred events
is an identity cache.  That cache used to live only in memory, so every restart
wiped it and the next poll treated the cluster's whole retention window as
brand new.  One roll of valhalla published 229 stale signals in a single poll
and buried a genuinely new warning twelfth in a queue that took hours to drain.

This is the same set, written down.  It is deliberately *not* the coalescing
index next door: that one folds observations together by structural shape, so
many distinct events share a slot.  Dedupe needs the opposite — exact identity,
one entry per event — because two different pods crash-looping are the same
shape but are not the same observation.

The file holds the most recent ``capacity`` identities, newest last, and is
rewritten atomically.  Callers hydrate it once at startup and keep the working
set in memory; nothing reads it on the hot path.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

SEEN_PREFIX = "resident/inbox/seen"

#: Matches the historical in-memory bound (``signal_dedupe_cache_size``), so
#: making the cache durable does not silently change how far back it reaches.
DEFAULT_SEEN_CAPACITY = 4096


class SeenSignalKeys:
    """Bounded, durable set of observation identities, newest-wins."""

    def __init__(
        self,
        root: Path | str,
        *,
        prefix: str = SEEN_PREFIX,
        capacity: int = DEFAULT_SEEN_CAPACITY,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._prefix = prefix.strip("/").strip() or SEEN_PREFIX
        self._capacity = max(1, int(capacity))

    @property
    def path(self) -> Path:
        return self._root / self._prefix / "keys.txt"

    def load(self) -> list[str]:
        """Return the recorded identities, oldest first.

        A missing file is an empty set — a resident that has never run has seen
        nothing.  An unreadable one is logged and also treated as empty: losing
        the record costs one replay of recent history, whereas refusing to start
        costs the resident everything.
        """
        path = self.path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            logger.warning(
                "resident inbox: could not read the seen-key record at %s; "
                "recent observations may be re-ingested once",
                path,
                exc_info=True,
            )
            return []
        return [line for line in (item.strip() for item in raw.splitlines()) if line]

    def record(self, keys: Iterable[str]) -> int:
        """Merge *keys* in as most-recent and persist, returning the stored count.

        Existing entries move to the newest end rather than duplicating, so a
        source that keeps re-reporting the same event does not push everything
        else out of the window.
        """
        merged: OrderedDict[str, None] = OrderedDict.fromkeys(self.load())
        added = 0
        for key in keys:
            cleaned = str(key or "").strip()
            if not cleaned:
                continue
            if cleaned in merged:
                merged.move_to_end(cleaned)
                continue
            merged[cleaned] = None
            added += 1
        while len(merged) > self._capacity:
            merged.popitem(last=False)
        if added:
            self._write(list(merged))
        return len(merged)

    def _write(self, keys: Sequence[str]) -> None:
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(keys) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # Never let bookkeeping break intake. The in-memory set still holds
            # for this process; only the restart benefit is lost.
            logger.warning(
                "resident inbox: could not persist the seen-key record at %s",
                path,
                exc_info=True,
            )
