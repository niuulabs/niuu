"""Letheo resident state adapter.

Letheo is a cognitive runtime rather than a ledger.  This adapter keeps Ravn's
typed resident state locally and feeds resident turns/artifacts into Letheo's
Python Session when the package is installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.domain.resident_continuation import ResidentMemoryEntry, ResidentTurnRecord
from ravn.resident_continuation import _compact_line, _render_turn_record


class LetheoResidentStateAdapter(LocalResidentState):
    """Resident state adapter with Letheo evocation as cognitive recall."""

    def __init__(
        self,
        root: Path | str,
        *,
        subject: str = "ravn:resident",
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        super().__init__(Path(root), continuation_prefix=continuation_prefix)
        self._subject = subject
        self._session = _load_letheo_session()

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        local = await super().recall(mandate, limit=limit)
        evoked = self._evoke(mandate)
        if not evoked:
            return local
        return (
            ResidentMemoryEntry(
                path=f"letheo:{self._subject}",
                summary="Letheo evoked resident context",
                content=evoked,
            ),
            *local,
        )[:limit]

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        ref = await super().write_turn(record)
        self._perceive(f"{ref}\n\n{_render_turn_record(record)}")
        return ref

    async def write_artifact(self, artifact, content: str) -> str:
        ref = await super().write_artifact(artifact, content)
        self._perceive(f"{ref}\n\n{content}")
        return ref

    async def write_consolidation(self, model, result) -> str:
        ref = await super().write_consolidation(model, result)
        path = self._root / ref
        self._perceive(path.read_text(encoding="utf-8"))
        return ref

    def _perceive(self, text: str) -> None:
        if hasattr(self._session, "perceive"):
            self._session.perceive(self._subject, act=text)
        if hasattr(self._session, "breathe"):
            self._session.breathe()

    def _evoke(self, query: str) -> str:
        query = _compact_line(query)
        if hasattr(self._session, "evoke_unified"):
            return str(self._session.evoke_unified(self._subject, query))
        if hasattr(self._session, "recall"):
            return str(self._session.recall(self._subject, query, k=3))
        return ""


def _load_letheo_session() -> Any:
    try:
        from letheo_orchestration import Session
    except ImportError as exc:
        raise RuntimeError(
            "LetheoResidentStateAdapter requires the letheo_orchestration package. "
            "Install/build Letheo before enabling this adapter."
        ) from exc
    return Session()

