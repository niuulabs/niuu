"""Resident inbox storage adapters."""

from __future__ import annotations

import asyncio
import logging
import os
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


class LocalResidentInbox(ResidentInboxBackend):
    """Durable filesystem inbox for residents that do not use Mimir.

    References deliberately use the same ``resident/inbox/...`` namespace as
    the Mimir adapter.  The runtime therefore depends only on
    :class:`ResidentInboxBackend`; choosing local files or Mimir is composition
    configuration, not resident behavior.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
        triage_prefix: str = _INBOX_TRIAGE_PREFIX,
        decision_prefix: str = _INBOX_DECISION_PREFIX,
        retention_max_pages: int = 500,
        retention_max_age_days: float = 7.0,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX
        self._triage_prefix = triage_prefix.strip("/").strip() or _INBOX_TRIAGE_PREFIX
        self._decision_prefix = decision_prefix.strip("/").strip() or _INBOX_DECISION_PREFIX
        self._retention_max_pages = max(0, int(retention_max_pages))
        self._retention_max_age_days = max(0.0, float(retention_max_age_days))
        self._lock = asyncio.Lock()

    async def write_event(self, event: Any) -> str:
        return await self.write_signal(signal_from_event(event))

    async def write_directed_message(
        self,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        source: str = "skuld:directed_message",
    ) -> str:
        return await self.write_signal(
            signal_from_directed_message(content, metadata=metadata, source=source)
        )

    async def write_signal(self, signal: ResidentInboxSignal) -> str:
        ref = f"{self._signal_prefix}/{_signal_filename(signal)}"
        async with self._lock:
            await asyncio.to_thread(self._write_signal_sync, ref, signal)
            await asyncio.to_thread(self._prune_signals_sync)
        return ref

    def _write_signal_sync(self, ref: str, signal: ResidentInboxSignal) -> None:
        path = self._path(ref)
        if path.exists():
            existing = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if (
                existing is not None
                and existing.status != ResidentInboxStatus.NEW.value
                and signal.status == ResidentInboxStatus.NEW.value
            ):
                return
        self._atomic_write(path, render_inbox_signal(signal))

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_signals_sync, status, max(0, limit))

    def _list_signals_sync(
        self,
        status: str,
        limit: int,
    ) -> list[tuple[str, ResidentInboxSignal]]:
        if limit == 0:
            return []
        directory = self._path(self._signal_prefix)
        if not directory.is_dir():
            return []
        items: list[tuple[str, ResidentInboxSignal]] = []
        for path in sorted(directory.glob("*.md"), reverse=True):
            signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if signal is None or (status and signal.status != status):
                continue
            items.append((str(path.relative_to(self._root)), signal))
            if len(items) >= limit:
                break
        return items

    async def write_triage(self, triage: ResidentInboxTriage) -> str:
        stamp = timestamp_slug(triage.created_at)
        ref = f"{self._triage_prefix}/{stamp}-{_slug(triage.signal_id) or 'signal'}.md"
        async with self._lock:
            await asyncio.to_thread(
                self._atomic_write,
                self._path(ref),
                render_inbox_triage(triage),
            )
        return ref

    async def append_decision(self, entry: str) -> str:
        stamp = timestamp_slug(datetime.now(UTC))
        ref = f"{self._decision_prefix}/{stamp}.md"
        async with self._lock:
            await asyncio.to_thread(
                self._atomic_write,
                self._path(ref),
                f"# Resident Inbox Decision\n\n{entry}\n",
            )
        return ref

    async def acknowledge(
        self,
        refs: tuple[str, ...],
        *,
        status: str = ResidentInboxStatus.REMEMBERED.value,
        reason: str = "resident turn recorded",
    ) -> tuple[str, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._acknowledge_sync, refs, status, reason)

    def _acknowledge_sync(
        self,
        refs: tuple[str, ...],
        status: str,
        reason: str,
    ) -> tuple[str, ...]:
        acknowledged: list[str] = []
        processed_at = datetime.now(UTC)
        for ref in refs:
            if not ref.startswith(f"{self._signal_prefix}/"):
                continue
            path = self._path(ref)
            if not path.is_file():
                continue
            signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if signal is None:
                continue
            self._atomic_write(
                path,
                render_inbox_signal(
                    signal.with_updates(
                        status=status,
                        reason=reason or signal.reason,
                        processed_at=processed_at,
                    )
                ),
            )
            acknowledged.append(ref)
        return tuple(acknowledged)

    async def prune_signals(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._prune_signals_sync)

    def _prune_signals_sync(self) -> int:
        directory = self._path(self._signal_prefix)
        if not directory.is_dir():
            return 0
        protected: list[tuple[float, Path]] = []
        processed: list[tuple[float, Path]] = []
        for path in directory.glob("*.md"):
            try:
                entry = (path.stat().st_mtime, path)
                signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if signal is None or signal.status == ResidentInboxStatus.NEW.value:
                protected.append(entry)
            else:
                processed.append(entry)
        doomed: set[Path] = set()
        if self._retention_max_age_days > 0:
            cutoff = time.time() - self._retention_max_age_days * 86400
            doomed.update(path for mtime, path in processed if mtime < cutoff)
        retained = [(mtime, path) for mtime, path in processed if path not in doomed]
        if self._retention_max_pages > 0:
            # The cap is best-effort while NEW or unreadable records exist. They
            # are delivery state, not history, and must never disappear merely
            # because a resident is slow or unavailable.
            processed_budget = max(0, self._retention_max_pages - len(protected))
            if len(retained) > processed_budget:
                retained.sort(key=lambda item: item[0], reverse=True)
                doomed.update(path for _mtime, path in retained[processed_budget:])
        pruned = 0
        for path in doomed:
            try:
                path.unlink()
                pruned += 1
            except FileNotFoundError:
                continue
        return pruned

    def _path(self, ref: str) -> Path:
        path = (self._root / ref).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(f"resident inbox reference escapes its root: {ref!r}") from exc
        return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


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
        self._retention_sweep_task: asyncio.Task[None] | None = None
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
            if self._retention_sweep_task is None or self._retention_sweep_task.done():
                delay = self._retention_sweep_interval_seconds - (now - self._last_retention_sweep)
                self._retention_sweep_task = asyncio.create_task(
                    self._delayed_retention_sweep(delay)
                )
            return
        await self._run_retention_sweep()

    async def _delayed_retention_sweep(self, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.0, delay))
            await self._run_retention_sweep()
        finally:
            self._retention_sweep_task = None

    async def _run_retention_sweep(self) -> None:
        self._last_retention_sweep = time.monotonic()
        try:
            pruned = await self.prune_signals()
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

    async def prune_signals(self) -> int:
        """Delete signal pages beyond the retention policy; return the count.

        The filesystem root is used only to identify the oldest pages. Actual
        deletion goes through Mimir so its catalog, graph, and search indexes
        stay consistent.
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
        doomed = await asyncio.to_thread(self._prunable_signal_paths, root)
        pruned = 0
        for path in doomed:
            try:
                if await self._mimir.delete_page(path):
                    pruned += 1
            except Exception as exc:
                logger.warning("resident inbox: failed to prune %s: %s", path, exc)
        return pruned

    def _prunable_signal_paths(self, root: Path) -> list[str]:
        wiki_dir = (root / "wiki").resolve()
        signals_dir = (wiki_dir / self._signal_prefix).resolve()
        try:
            signals_dir.relative_to(wiki_dir)
        except ValueError:
            logger.warning("resident inbox: signal prefix escapes the Mimir wiki root")
            return []
        if not signals_dir.is_dir():
            return []

        protected_count = 0
        processed: list[tuple[float, str]] = []
        for page in signals_dir.glob("*.md"):
            try:
                resolved = page.resolve()
                relative = str(resolved.relative_to(wiki_dir))
                signal = parse_inbox_signal(resolved.read_text(encoding="utf-8"))
                entry = (resolved.stat().st_mtime, relative)
            except (OSError, UnicodeError, ValueError):
                protected_count += 1
                continue
            if signal is None or signal.status == ResidentInboxStatus.NEW.value:
                protected_count += 1
            else:
                processed.append(entry)

        doomed: list[str] = []
        if self._retention_max_age_days > 0:
            cutoff = time.time() - self._retention_max_age_days * 86400
            doomed.extend(page for mtime, page in processed if mtime < cutoff)
            processed = [(mtime, page) for mtime, page in processed if mtime >= cutoff]
        if self._retention_max_pages > 0:
            processed_budget = max(0, self._retention_max_pages - protected_count)
            if len(processed) > processed_budget:
                processed.sort(key=lambda item: item[0], reverse=True)
                doomed.extend(page for _, page in processed[processed_budget:])
        return doomed

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

    async def acknowledge(
        self,
        refs: tuple[str, ...],
        *,
        status: str = ResidentInboxStatus.REMEMBERED.value,
        reason: str = "resident turn recorded",
    ) -> tuple[str, ...]:
        """Mark exact inbox pages consumed after their resident turn is durable."""
        acknowledged: list[str] = []
        processed_at = datetime.now(UTC)
        for path in refs:
            if not path.startswith(f"{self._signal_prefix}/"):
                continue
            try:
                signal = parse_inbox_signal(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if signal is None:
                continue
            updated = signal.with_updates(
                status=status,
                reason=reason or signal.reason,
                processed_at=processed_at,
            )
            await self._mimir.upsert_page(path, render_inbox_signal(updated))
            acknowledged.append(path)
        return tuple(acknowledged)
