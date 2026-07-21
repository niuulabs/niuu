"""Shared helpers for canonical Ravn help-needed events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ravn.domain.events import RavnEvent


def build_help_needed_event(
    *,
    source: str,
    persona: str,
    reason: str,
    summary: str,
    attempted: Iterable[str] = (),
    recommendation: str,
    correlation_id: str,
    session_id: str = "",
    task_id: str = "",
    context: Mapping[str, Any] | None = None,
    trace_context: Mapping[str, str] | None = None,
) -> RavnEvent:
    """Build the canonical event emitted when Ravn needs human input."""
    return RavnEvent.help_needed(
        source=source,
        persona=persona,
        reason=reason,
        summary=summary,
        attempted=[str(item) for item in attempted if str(item).strip()],
        recommendation=recommendation,
        correlation_id=correlation_id,
        session_id=session_id,
        task_id=task_id or correlation_id,
        context=dict(context or {}),
        trace_context=dict(trace_context or {}),
    )
