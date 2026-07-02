"""Resident signal source adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ravn.ports.mimir import MimirPort
from ravn.ports.resident_signal import (
    ResidentSignalCandidateSourcePort,
    ResidentSignalSourcePort,
)
from ravn.resident_inbox.models import (
    _INBOX_SIGNAL_PREFIX,
    ResidentInboxClassification,
    ResidentInboxSignal,
)
from ravn.resident_inbox.serialization import parse_inbox_signal
from ravn.resident_text import slug


class MarkdownResidentSignalSource(ResidentSignalSourcePort):
    """Loads a local markdown file as a manual resident signal."""

    async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal:
        path = Path(ref_or_id).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)
        content = path.read_text(encoding="utf-8")
        raw_ref = str(path)
        created_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        return ResidentInboxSignal(
            id=f"markdown-{slug(path.stem, fallback='signal')}",
            source="local:markdown",
            kind="manual.markdown",
            summary=f"Markdown resident signal from {path.name}",
            payload={"content": content},
            raw_ref=raw_ref,
            classification=ResidentInboxClassification.SOURCE_EVIDENCE.value,
            evidence_refs=(raw_ref,),
            created_at=created_at,
        )


class MimirResidentInboxSignalSource(
    ResidentSignalSourcePort,
    ResidentSignalCandidateSourcePort,
):
    """Loads stored resident inbox signals from Mimir."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
    ) -> None:
        self._mimir = mimir
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX

    async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal:
        text = ref_or_id.strip()
        signal = await self._read_signal_page(text)
        if signal is not None:
            return signal

        for ref in await self._signal_refs():
            signal = await self._read_signal_page(ref)
            if signal is None:
                continue
            if signal.id == text:
                return signal
        raise FileNotFoundError(text)

    async def list_candidates(
        self,
        *,
        limit: int,
        status: str = "",
        classification: str = "",
    ) -> list[tuple[str, ResidentInboxSignal]]:
        items: list[tuple[str, ResidentInboxSignal]] = []
        for ref in await self._signal_refs():
            signal = await self._read_signal_page(ref)
            if signal is None:
                continue
            if status and signal.status != status:
                continue
            if classification and signal.classification != classification:
                continue
            items.append((ref, signal))
            if len(items) >= limit:
                break
        return items

    async def _signal_refs(self) -> list[str]:
        pages = await self._mimir.list_pages(prefix=self._signal_prefix)
        refs = [str(getattr(page, "path", "") or "") for page in pages]
        return sorted((ref for ref in refs if ref), reverse=True)

    async def _read_signal_page(self, ref: str) -> ResidentInboxSignal | None:
        if not ref:
            return None
        try:
            signal = parse_inbox_signal(await self._mimir.read_page(ref))
        except FileNotFoundError:
            return None
        if signal is None:
            return None
        return signal.with_updates(raw_ref=signal.raw_ref or ref)
