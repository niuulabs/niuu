"""Resident inbox storage adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.ports.mimir import MimirPort
from ravn.resident_text import slug as _slug
from ravn.resident_text import timestamp_slug

from .archive import ARCHIVE_PREFIX, RawSignalArchive, archive_ref_sort_key
from .models import (
    _INBOX_DECISION_PREFIX,
    _INBOX_PENDING_PREFIX,
    _INBOX_PROCESSED_PREFIX,
    _INBOX_SIGNAL_PREFIX,
    _INBOX_TRIAGE_PREFIX,
    _OPERATOR_DIRECTED_MESSAGE_KIND,
    ResidentInboxBackend,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .seen import DEFAULT_SEEN_CAPACITY, SEEN_PREFIX, SeenSignalKeys
from .serialization import (
    _signal_filename,
    _signal_from_dict,
    _signal_to_dict,
    parse_inbox_signal,
    render_inbox_signal,
    render_inbox_triage,
    signal_from_directed_message,
    signal_from_event,
)
from .shape import ShapeAggregate, fold_aggregate, numeric_novelty, shape_key

logger = logging.getLogger(__name__)


class LocalResidentInbox(ResidentInboxBackend):
    """Durable filesystem inbox with an append-only archive and a bounded queue.

    Two stores with different jobs:

    ``raw/``
        Append-only NDJSON of every observation ever received.  Nothing deletes
        from it and nothing reads it on the write path.  It is the durable
        evidence the queue's aggregates summarise.

    ``pending/`` and ``processed/``
        The operational queue.  ``pending/`` holds at most one *slot* per
        structural shape: a further observation of a shape the resident has not
        looked at yet folds into the existing slot instead of taking another
        queue position.  Judged slots move to ``processed/`` and become subject
        to retention.

    Coalescing only ever merges observations the resident has not yet seen, so
    when the resident is keeping up nothing is coalesced at all.  The collapse
    rate measures how far behind it is; it never decides that anything is
    unimportant.

    References deliberately use the same ``resident/inbox/...`` namespace as the
    Mimir adapter.  The runtime therefore depends only on
    :class:`ResidentInboxBackend`; choosing local files or Mimir is composition
    configuration, not resident behavior.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
        pending_prefix: str = _INBOX_PENDING_PREFIX,
        processed_prefix: str = _INBOX_PROCESSED_PREFIX,
        archive_prefix: str = ARCHIVE_PREFIX,
        triage_prefix: str = _INBOX_TRIAGE_PREFIX,
        decision_prefix: str = _INBOX_DECISION_PREFIX,
        retention_max_pages: int = 500,
        retention_max_age_days: float = 7.0,
        retention_sweep_interval_seconds: float = 900.0,
        max_distinct_values: int = 24,
        novelty_min_observations: int = 20,
        max_invalid_attempts: int = 3,
        pending_slot_warn_threshold: int = 200,
        seen_prefix: str = SEEN_PREFIX,
        seen_capacity: int = DEFAULT_SEEN_CAPACITY,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX
        self._pending_prefix = pending_prefix.strip("/").strip() or _INBOX_PENDING_PREFIX
        self._processed_prefix = processed_prefix.strip("/").strip() or _INBOX_PROCESSED_PREFIX
        self._triage_prefix = triage_prefix.strip("/").strip() or _INBOX_TRIAGE_PREFIX
        self._decision_prefix = decision_prefix.strip("/").strip() or _INBOX_DECISION_PREFIX
        self._retention_max_pages = max(0, int(retention_max_pages))
        self._retention_max_age_days = max(0.0, float(retention_max_age_days))
        self._retention_sweep_interval_seconds = max(0.0, float(retention_sweep_interval_seconds))
        self._max_distinct_values = max(1, int(max_distinct_values))
        self._novelty_min_observations = max(1, int(novelty_min_observations))
        self._max_invalid_attempts = max(1, int(max_invalid_attempts))
        self._pending_slot_warn_threshold = max(1, int(pending_slot_warn_threshold))
        self._archive = RawSignalArchive(self._root, prefix=archive_prefix)
        self._seen = SeenSignalKeys(self._root, prefix=seen_prefix, capacity=seen_capacity)
        self._last_retention_sweep: float | None = None
        self._warned_pending_slots = False
        self._lock = asyncio.Lock()

    @property
    def archive(self) -> RawSignalArchive:
        """The append-only record of every observation this inbox received."""
        return self._archive

    # -- durable dedupe -------------------------------------------------

    async def load_seen_signal_keys(self) -> list[str]:
        return await asyncio.to_thread(self._seen.load)

    async def record_seen_signal_keys(self, keys: Sequence[str]) -> None:
        await asyncio.to_thread(self._seen.record, list(keys))

    # -- intake ---------------------------------------------------------

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
        async with self._lock:
            ref = await asyncio.to_thread(self._write_signal_sync, signal)
        await self._maybe_prune_signals()
        return ref

    def _write_signal_sync(self, signal: ResidentInboxSignal) -> str:
        # Durability first: the observation is archived before any queue
        # decision, so a coalesced slot always summarises records that exist.
        identity = _slot_identity(signal)
        key = _shape_of(signal)
        archive_ref = self._archive.append({"shape_key": key, "signal": _signal_to_dict(signal)})

        if signal.status != ResidentInboxStatus.NEW.value:
            # Already-judged records are history, not queue work.
            ref = f"{self._processed_prefix}/{_signal_filename(signal)}"
            self._atomic_write(
                self._path(ref),
                render_inbox_signal(
                    signal.with_updates(
                        first_archive_ref=archive_ref,
                        last_archive_ref=archive_ref,
                        first_observed_at=signal.observed_at,
                    )
                ),
            )
            return ref

        ref = f"{self._pending_prefix}/{key}.md"
        path = self._path(ref)

        if identity:
            # Identity-scoped slots (directed operator messages) dedupe by
            # identity rather than coalescing: a replayed message is the same
            # message, and an already-judged one must not be resurrected.
            judged = self._find_processed_slot(key)
            if judged is not None:
                return judged

        existing = None
        if path.is_file():
            existing = parse_inbox_signal(path.read_text(encoding="utf-8"))
        if existing is not None:
            novelty = numeric_novelty(
                existing.aggregate,
                signal.payload,
                observation_count=existing.observation_count,
                min_observations=self._novelty_min_observations,
            )
            if novelty is not None:
                # Structurally identical but numerically outside everything this
                # slot has seen. Folding it would bury an excursion behind a
                # widened bound, so it takes its own slot and wakes the resident.
                novel_path, novel_value = novelty
                logger.info(
                    "resident inbox: %s=%s is outside the established range for shape %s; "
                    "routing to its own slot",
                    novel_path,
                    novel_value,
                    key,
                )
                key = f"{key}-novel"
                ref = f"{self._pending_prefix}/{key}.md"
                path = self._path(ref)
                existing = (
                    parse_inbox_signal(path.read_text(encoding="utf-8")) if path.is_file() else None
                )
        slot = self._fold_slot(existing, signal, archive_ref, shape=key)
        self._atomic_write(path, render_inbox_signal(slot))
        if existing is None:
            # Only a brand new shape can grow the queue, so this is the only
            # write that needs to count slots. Folding writes stay O(1).
            self._warn_on_unbounded_shapes()
        return ref

    def _fold_slot(
        self,
        existing: ResidentInboxSignal | None,
        signal: ResidentInboxSignal,
        archive_ref: str,
        *,
        shape: str,
    ) -> ResidentInboxSignal:
        observed_at = signal.observed_at or signal.created_at.isoformat()
        if existing is None:
            return signal.with_updates(
                shape_key=shape,
                observation_count=1,
                first_archive_ref=archive_ref,
                last_archive_ref=archive_ref,
                first_observed_at=observed_at,
                observed_at=observed_at,
                aggregate=fold_aggregate(
                    ShapeAggregate(),
                    signal.payload,
                    archive_ref=archive_ref,
                    max_distinct_values=self._max_distinct_values,
                ),
            )
        # The newest observation becomes the slot's face; the aggregate carries
        # everything the earlier ones varied by, so nothing is lost by showing
        # the resident the most recent payload.
        return existing.with_updates(
            summary=signal.summary,
            payload=signal.payload,
            trace_context=signal.trace_context or existing.trace_context,
            raw_ref=signal.raw_ref or existing.raw_ref,
            classification=signal.classification,
            confidence=signal.confidence,
            reason=signal.reason,
            observed_at=observed_at,
            shape_key=shape,
            observation_count=existing.observation_count + 1,
            last_archive_ref=archive_ref,
            aggregate=fold_aggregate(
                existing.aggregate,
                signal.payload,
                archive_ref=archive_ref,
                max_distinct_values=self._max_distinct_values,
            ),
        )

    # -- selection ------------------------------------------------------

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
        wants_pending = not status or status == ResidentInboxStatus.NEW.value
        wants_processed = not status or status != ResidentInboxStatus.NEW.value
        items: list[tuple[str, ResidentInboxSignal]] = []

        if wants_pending:
            # Oldest-first: a slot that has been waiting longest is served
            # first, so nothing starves behind newer arrivals. The queue is
            # bounded by distinct shapes, so reading every slot stays cheap.
            slots = self._read_directory(self._pending_prefix, status="")
            slots.sort(key=lambda row: (row[1].first_observed_at, row[0]))
            items.extend(slots)

        if wants_processed and len(items) < limit:
            processed = self._read_directory(self._processed_prefix, status=status, newest=True)
            items.extend(processed)

        return items[:limit]

    def _read_directory(
        self,
        prefix: str,
        *,
        status: str,
        newest: bool = False,
    ) -> list[tuple[str, ResidentInboxSignal]]:
        directory = self._path(prefix)
        if not directory.is_dir():
            return []
        rows: list[tuple[str, ResidentInboxSignal]] = []
        for path in sorted(directory.glob("*.md"), reverse=newest):
            signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if signal is None or (status and signal.status != status):
                continue
            rows.append((str(path.relative_to(self._root)), signal))
        return rows

    # -- judgment -------------------------------------------------------

    async def acknowledge(
        self,
        refs: tuple[str, ...],
        *,
        status: str = ResidentInboxStatus.REMEMBERED.value,
        reason: str = "resident turn recorded",
        expected: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        async with self._lock:
            acknowledged = await asyncio.to_thread(
                self._acknowledge_sync, refs, status, reason, dict(expected or {})
            )
        await self._maybe_prune_signals()
        return acknowledged

    def _acknowledge_sync(
        self,
        refs: tuple[str, ...],
        status: str,
        reason: str,
        expected: dict[str, str],
    ) -> tuple[str, ...]:
        acknowledged: list[str] = []
        processed_at = datetime.now(UTC)
        for ref in refs:
            path = self._resolve_ref(ref)
            if path is None or not path.is_file():
                continue
            signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if signal is None:
                continue
            if signal.status != ResidentInboxStatus.NEW.value:
                # Already judged: acknowledging twice is a no-op, not an error.
                acknowledged.append(ref)
                continue
            judged_through = expected.get(ref, signal.last_archive_ref)
            remainder = self._pending_remainder(signal, judged_through)
            judged = signal.with_updates(
                status=status,
                reason=reason or signal.reason,
                processed_at=processed_at,
                last_archive_ref=judged_through,
                observation_count=max(1, signal.observation_count - _count(remainder)),
            )
            self._atomic_write(
                self._path(f"{self._processed_prefix}/{_slot_filename(judged)}"),
                render_inbox_signal(judged),
            )
            if remainder is None:
                path.unlink(missing_ok=True)
            else:
                # Observations that arrived while the resident was judging stay
                # pending. It never acknowledges what it did not see.
                self._atomic_write(path, render_inbox_signal(remainder))
            acknowledged.append(ref)
        return tuple(acknowledged)

    def _pending_remainder(
        self,
        slot: ResidentInboxSignal,
        judged_through: str,
    ) -> ResidentInboxSignal | None:
        """Rebuild the un-judged tail of a slot from durable archive records."""
        if not judged_through or judged_through == slot.last_archive_ref:
            return None
        if archive_ref_sort_key(judged_through) >= archive_ref_sort_key(slot.last_archive_ref):
            return None
        records = self._archive.read_range(
            after=judged_through,
            through=slot.last_archive_ref,
            limit=max(1, slot.observation_count),
            shape_key=slot.shape_key,
        )
        rebuilt: ResidentInboxSignal | None = None
        for archive_ref, record in records:
            payload = record.get("signal")
            if not isinstance(payload, dict):
                continue
            observation = _signal_from_dict(payload)
            # Recomputed, never trusted from the record: the archive interleaves
            # every shape, and only this slot's own observations may fold back in.
            if _shape_of(observation) != slot.shape_key:
                continue
            rebuilt = self._fold_slot(rebuilt, observation, archive_ref, shape=slot.shape_key)
        return rebuilt

    async def record_failed_attempt(
        self,
        refs: tuple[str, ...],
        *,
        reason: str,
    ) -> tuple[str, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._record_failed_attempt_sync, refs, reason)

    def _record_failed_attempt_sync(
        self,
        refs: tuple[str, ...],
        reason: str,
    ) -> tuple[str, ...]:
        blocked: list[str] = []
        processed_at = datetime.now(UTC)
        for ref in refs:
            path = self._resolve_ref(ref)
            if path is None or not path.is_file():
                continue
            signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            if signal is None or signal.status != ResidentInboxStatus.NEW.value:
                continue
            attempts = signal.attempts + 1
            if attempts < self._max_invalid_attempts:
                self._atomic_write(
                    path, render_inbox_signal(signal.with_updates(attempts=attempts))
                )
                continue
            # No resident turn has been able to judge this slot. Stop retrying
            # and make it visible to a human instead of looping forever.
            stuck = signal.with_updates(
                status=ResidentInboxStatus.BLOCKED.value,
                attempts=attempts,
                reason=reason,
                processed_at=processed_at,
            )
            self._atomic_write(
                self._path(f"{self._processed_prefix}/{_slot_filename(stuck)}"),
                render_inbox_signal(stuck),
            )
            path.unlink(missing_ok=True)
            blocked.append(ref)
        return tuple(blocked)

    # -- records --------------------------------------------------------

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

    # -- retention ------------------------------------------------------

    async def _maybe_prune_signals(self) -> None:
        """Sweep when one is due. Never on the write path, never blocking."""
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
            await self.prune_signals()
        except Exception:
            logger.warning("resident inbox: retention sweep failed", exc_info=True)

    async def prune_signals(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._prune_processed_sync)

    def _prune_processed_sync(self) -> int:
        """Delete judged records beyond the retention policy; return the count.

        Only ``processed/`` is considered.  Pending slots are delivery state and
        the raw archive is the only surviving copy of the history, so neither is
        ever eligible — this is enforced by which directory is walked, not by a
        status check that a future edit could weaken.
        """
        directory = self._path(self._processed_prefix)
        if not directory.is_dir():
            return 0
        records: list[tuple[float, Path]] = []
        for path in directory.glob("*.md"):
            try:
                records.append((path.stat().st_mtime, path))
            except OSError:
                continue
        doomed: set[Path] = set()
        if self._retention_max_age_days > 0:
            cutoff = time.time() - self._retention_max_age_days * 86400
            doomed.update(path for mtime, path in records if mtime < cutoff)
        retained = [(mtime, path) for mtime, path in records if path not in doomed]
        if self._retention_max_pages > 0 and len(retained) > self._retention_max_pages:
            retained.sort(key=lambda item: item[0], reverse=True)
            doomed.update(path for _mtime, path in retained[self._retention_max_pages :])
        pruned = 0
        for path in doomed:
            try:
                path.unlink()
                pruned += 1
            except FileNotFoundError:
                continue
        return pruned

    # -- migration ------------------------------------------------------

    async def migrate_flat_layout(self) -> dict[str, int]:
        """Move pre-coalescing flat signal files into the archive and queue.

        Resumable and non-destructive: each record is archived and re-filed
        before its original file is removed, so an interrupted run simply
        resumes.  Returns reconciliation counts for the operator to check.
        """
        async with self._lock:
            return await asyncio.to_thread(self._migrate_flat_layout_sync)

    def _migrate_flat_layout_sync(self) -> dict[str, int]:
        directory = self._path(self._signal_prefix)
        counts = {"read": 0, "archived": 0, "pending": 0, "processed": 0, "unreadable": 0}
        if not directory.is_dir():
            return counts
        # Oldest first, so folded slots keep a truthful archive range.
        for path in sorted(directory.glob("*.md")):
            counts["read"] += 1
            try:
                signal = parse_inbox_signal(path.read_text(encoding="utf-8"))
            except OSError:
                counts["unreadable"] += 1
                continue
            if signal is None:
                counts["unreadable"] += 1
                continue
            self._write_signal_sync(signal)
            counts["archived"] += 1
            if signal.status == ResidentInboxStatus.NEW.value:
                counts["pending"] += 1
            else:
                counts["processed"] += 1
            path.unlink(missing_ok=True)
        return counts

    # -- paths ----------------------------------------------------------

    def _find_processed_slot(self, shape: str) -> str | None:
        directory = self._path(self._processed_prefix)
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob(f"*-{shape}.md")):
            return str(path.relative_to(self._root))
        return None

    def _resolve_ref(self, ref: str) -> Path | None:
        """Resolve a reference written by any layout this inbox has used.

        A slot's reference changes when it is judged, and turn records keep the
        pending reference they were built from.  Resolution therefore follows a
        judged slot to ``processed/`` rather than reporting it missing.
        """
        direct = self._path(ref)
        if direct.is_file():
            return direct
        name = ref.rsplit("/", 1)[-1]
        for prefix in (self._pending_prefix, self._processed_prefix):
            if ref.startswith(f"{prefix}/"):
                continue
            candidate = self._path(f"{prefix}/{name}")
            if candidate.is_file():
                return candidate
        if ref.startswith(f"{self._pending_prefix}/"):
            # Processed slot files carry a timestamp prefix, so a pending
            # reference is followed by its shape rather than by its filename.
            judged = self._find_processed_slot(name.removesuffix(".md"))
            if judged is not None:
                return self._path(judged)
        return None

    def _warn_on_unbounded_shapes(self) -> None:
        if self._warned_pending_slots:
            return
        directory = self._path(self._pending_prefix)
        if not directory.is_dir():
            return
        count = sum(1 for _ in directory.glob("*.md"))
        if count < self._pending_slot_warn_threshold:
            return
        self._warned_pending_slots = True
        logger.warning(
            "resident inbox: %d pending shape slots exceeds the expected bound (%d). "
            "A source is likely emitting variable field names, which defeats "
            "coalescing and lets the queue grow with volume.",
            count,
            self._pending_slot_warn_threshold,
        )

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


def _shape_of(signal: ResidentInboxSignal) -> str:
    """The slot key one observation belongs to, derived only from its content."""
    return shape_key(
        source=signal.source,
        kind=signal.kind,
        payload=signal.payload,
        distinct_id=_slot_identity(signal),
    )


def _slot_identity(signal: ResidentInboxSignal) -> str:
    """Return the identity that forces a dedicated slot, or "" to coalesce.

    Directed operator messages carry authority and must never be folded into a
    shared slot or summarised away, so each one keeps its own identity.
    """
    if signal.kind == _OPERATOR_DIRECTED_MESSAGE_KIND:
        return signal.id
    return ""


def _slot_filename(slot: ResidentInboxSignal) -> str:
    stamp = _slug((slot.first_observed_at or slot.observed_at).replace("+00:00", "Z"))
    stamp = stamp or slot.created_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{slot.shape_key or _slug(slot.id) or 'signal'}.md"


def _count(remainder: ResidentInboxSignal | None) -> int:
    return remainder.observation_count if remainder is not None else 0


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
        max_invalid_attempts: int = 3,
    ) -> None:
        self._mimir = mimir
        self._max_invalid_attempts = max(1, int(max_invalid_attempts))
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
        expected: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """Mark exact inbox pages consumed after their resident turn is durable.

        This adapter does not coalesce, so one page is one observation and
        ``expected`` can only ever agree; a mismatch means the page was replaced
        underneath the caller and is left pending rather than acknowledged.
        """
        acknowledged: list[str] = []
        processed_at = datetime.now(UTC)
        expected = dict(expected or {})
        for path in refs:
            if not path.startswith(f"{self._signal_prefix}/"):
                continue
            try:
                signal = parse_inbox_signal(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if signal is None:
                continue
            if path in expected and expected[path] != signal.last_archive_ref:
                continue
            updated = signal.with_updates(
                status=status,
                reason=reason or signal.reason,
                processed_at=processed_at,
            )
            await self._mimir.upsert_page(path, render_inbox_signal(updated))
            acknowledged.append(path)
        return tuple(acknowledged)

    async def record_failed_attempt(
        self,
        refs: tuple[str, ...],
        *,
        reason: str,
    ) -> tuple[str, ...]:
        """Count one invalid resident outcome; return pages that became blocked."""
        blocked: list[str] = []
        processed_at = datetime.now(UTC)
        for path in refs:
            if not path.startswith(f"{self._signal_prefix}/"):
                continue
            try:
                signal = parse_inbox_signal(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if signal is None or signal.status != ResidentInboxStatus.NEW.value:
                continue
            attempts = signal.attempts + 1
            if attempts < self._max_invalid_attempts:
                await self._mimir.upsert_page(
                    path, render_inbox_signal(signal.with_updates(attempts=attempts))
                )
                continue
            await self._mimir.upsert_page(
                path,
                render_inbox_signal(
                    signal.with_updates(
                        status=ResidentInboxStatus.BLOCKED.value,
                        attempts=attempts,
                        reason=reason,
                        processed_at=processed_at,
                    )
                ),
            )
            blocked.append(path)
        return tuple(blocked)
