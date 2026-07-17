"""Mimir-backed resident inbox storage adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.ports.mimir import MimirPort
from ravn.resident_text import slug as _slug
from ravn.resident_text import timestamp_slug

from .models import (
    _INBOX_DECISION_PREFIX,
    _INBOX_SIGNAL_PREFIX,
    _INBOX_TRIAGE_PREFIX,
    ResidentInboxBackend,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .serialization import (
    _signal_filename,
    parse_inbox_signal,
    render_inbox_signal,
    render_inbox_triage,
    signal_from_directed_message,
    signal_from_event,
)

logger = logging.getLogger(__name__)


class MimirResidentInbox(ResidentInboxBackend):
    """Mimir-backed resident inbox under ``resident/inbox/...``."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
        triage_prefix: str = _INBOX_TRIAGE_PREFIX,
        decision_prefix: str = _INBOX_DECISION_PREFIX,
        retention_max_pages: int = 500,
        retention_max_age_days: float = 7.0,
        retention_sweep_interval_seconds: float = 900.0,
    ) -> None:
        self._mimir = mimir
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX
        self._triage_prefix = triage_prefix.strip("/").strip() or _INBOX_TRIAGE_PREFIX
        self._decision_prefix = decision_prefix.strip("/").strip() or _INBOX_DECISION_PREFIX
        self._retention_max_pages = retention_max_pages
        self._retention_max_age_days = retention_max_age_days
        self._retention_sweep_interval_seconds = retention_sweep_interval_seconds
        self._last_retention_sweep: float | None = None
        self._warned_no_filesystem = False

    async def write_event(self, event: Any) -> str:
        signal = signal_from_event(event)
        return await self.write_signal(signal)

    async def write_directed_message(
        self,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        source: str = "skuld:directed_message",
    ) -> str:
        signal = signal_from_directed_message(content, metadata=metadata, source=source)
        return await self.write_signal(signal)

    async def write_signal(self, signal: ResidentInboxSignal) -> str:
        path = f"{self._signal_prefix}/{_signal_filename(signal)}"
        try:
            existing = parse_inbox_signal(await self._mimir.read_page(path))
        except FileNotFoundError:
            existing = None
        if (
            existing is not None
            and existing.status != ResidentInboxStatus.NEW.value
            and signal.status == ResidentInboxStatus.NEW.value
        ):
            return path
        await self._mimir.upsert_page(path, render_inbox_signal(signal))
        await self._maybe_prune_signals()
        return path

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]:
        metas = await self._mimir.list_pages(prefix=self._signal_prefix)
        items: list[tuple[str, ResidentInboxSignal]] = []
        for meta in sorted(metas, key=lambda page: getattr(page, "path", ""), reverse=True):
            path = str(getattr(meta, "path", "") or "")
            if not path:
                continue
            try:
                signal = parse_inbox_signal(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if signal is None:
                continue
            if status and signal.status != status:
                continue
            items.append((path, signal))
            if len(items) >= limit:
                break
        return items

    async def _maybe_prune_signals(self) -> None:
        """Run a retention sweep when one is due; never blocks or raises.

        The inbox is a rolling working set, not an archive — signal pages are
        write-only operational records that otherwise grow without bound and
        poison mimir search/list over the wiki (NIU-1118). Sweeps are
        throttled and run in a worker thread off the write path; failures are
        logged loudly but must never break signal recording.
        """
        if self._retention_max_pages <= 0 and self._retention_max_age_days <= 0:
            return
        now = time.monotonic()
        if (
            self._last_retention_sweep is not None
            and now - self._last_retention_sweep < self._retention_sweep_interval_seconds
        ):
            return
        self._last_retention_sweep = now
        try:
            pruned = await asyncio.to_thread(self.prune_signals)
        except Exception:
            logger.warning("resident inbox: retention sweep failed", exc_info=True)
            return
        if pruned:
            logger.info(
                "resident inbox: pruned %d signal page(s) (max_pages=%d, max_age_days=%s)",
                pruned,
                self._retention_max_pages,
                self._retention_max_age_days,
            )

    def prune_signals(self) -> int:
        """Delete signal pages beyond the retention policy; return the count.

        Retention only works on filesystem-backed Mimir stores: the port has
        no delete operation, but ``filesystem_root()`` is part of its contract
        and the markdown adapter keeps pages as plain files under ``wiki/``
        with no index to invalidate. Non-filesystem backends log one warning
        and skip.
        """
        root = self._mimir.filesystem_root()
        if root is None:
            if not self._warned_no_filesystem:
                self._warned_no_filesystem = True
                logger.warning(
                    "resident inbox: Mimir backend is not filesystem-backed; "
                    "signal retention cannot run and the inbox will grow unbounded"
                )
            return 0
        signals_dir = root / "wiki" / self._signal_prefix
        if not signals_dir.is_dir():
            return 0

        entries: list[tuple[float, Path]] = []
        for page in signals_dir.glob("*.md"):
            try:
                entries.append((page.stat().st_mtime, page))
            except OSError:
                continue

        doomed: list[Path] = []
        if self._retention_max_age_days > 0:
            cutoff = time.time() - self._retention_max_age_days * 86400
            doomed.extend(page for mtime, page in entries if mtime < cutoff)
            entries = [(mtime, page) for mtime, page in entries if mtime >= cutoff]
        if self._retention_max_pages > 0 and len(entries) > self._retention_max_pages:
            entries.sort(key=lambda item: item[0], reverse=True)
            doomed.extend(page for _, page in entries[self._retention_max_pages :])

        pruned = 0
        for page in doomed:
            try:
                page.unlink()
                pruned += 1
            except OSError as exc:
                logger.warning("resident inbox: failed to prune %s: %s", page, exc)
        return pruned

    async def write_triage(self, triage: ResidentInboxTriage) -> str:
        stamp = timestamp_slug(triage.created_at)
        slug = _slug(triage.signal_id) or "signal"
        path = f"{self._triage_prefix}/{stamp}-{slug}.md"
        await self._mimir.upsert_page(path, render_inbox_triage(triage))
        return path

    async def append_decision(self, entry: str) -> str:
        stamp = timestamp_slug(datetime.now(UTC))
        path = f"{self._decision_prefix}/{stamp}.md"
        await self._mimir.upsert_page(path, f"# Resident Inbox Decision\n\n{entry}\n")
        return path
