"""Mimir-backed resident inbox storage adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_opportunity import ResidentOpportunitySignal
from ravn.domain.resident_portfolio import ResidentObjective
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import _slug
from ravn.resident_text import timestamp_slug

from .classify import _keywords
from .models import (
    _INBOX_DECISION_PREFIX,
    _INBOX_SIGNAL_PREFIX,
    _INBOX_TRIAGE_PREFIX,
    ResidentInboxBackend,
    ResidentInboxClassification,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .routing import _inbox_outcomes
from .serialization import (
    _signal_filename,
    parse_inbox_signal,
    render_inbox_signal,
    render_inbox_triage,
    signal_from_directed_message,
    signal_from_event,
)


class MimirResidentInbox(ResidentInboxBackend):
    """Mimir-backed resident inbox under ``resident/inbox/...``."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
        triage_prefix: str = _INBOX_TRIAGE_PREFIX,
        decision_prefix: str = _INBOX_DECISION_PREFIX,
    ) -> None:
        self._mimir = mimir
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX
        self._triage_prefix = triage_prefix.strip("/").strip() or _INBOX_TRIAGE_PREFIX
        self._decision_prefix = decision_prefix.strip("/").strip() or _INBOX_DECISION_PREFIX

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

    async def collect(
        self,
        *,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
        limit: int,
    ) -> tuple[ResidentOpportunitySignal, ...]:
        del mandate, domain_model
        if limit <= 0:
            return ()
        used_refs = {
            source_evidence
            for objective in objectives
            for source_evidence in objective.source_evidence
        }
        rows = await self.list_signals(status="", limit=max(limit * 4, limit))
        signals: list[ResidentOpportunitySignal] = []
        for path, signal in rows:
            if any(path in source_evidence for source_evidence in used_refs):
                continue
            if signal.classification not in {
                ResidentInboxClassification.IDEA.value,
                ResidentInboxClassification.SOURCE_EVIDENCE.value,
                ResidentInboxClassification.TASK_REQUEST.value,
                ResidentInboxClassification.URL_REFERENCE.value,
                ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
            }:
                continue
            signals.append(
                ResidentOpportunitySignal(
                    id=signal.id,
                    source=signal.source,
                    kind=signal.kind,
                    summary=signal.summary,
                    evidence_ref=path,
                    themes=tuple(_keywords(signal.summary)[:4]),
                    outcomes=_inbox_outcomes(signal),
                    confidence=signal.confidence,
                )
            )
            if len(signals) >= limit:
                break
        return tuple(signals)

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
