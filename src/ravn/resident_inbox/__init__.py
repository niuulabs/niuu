"""Resident inbox intake, triage, and portfolio conversion."""

from __future__ import annotations

from .backend import MimirResidentInbox
from .classify import classify_inbox_signal, classify_text
from .models import (
    ResidentInboxBackend,
    ResidentInboxClassification,
    ResidentInboxConfig,
    ResidentInboxRun,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .runtime import ResidentInboxRuntime
from .serialization import (
    parse_inbox_signal,
    render_inbox_signal,
    render_inbox_triage,
    signal_from_directed_message,
    signal_from_event,
    signal_from_event_like_record,
)

__all__ = [
    "MimirResidentInbox",
    "ResidentInboxBackend",
    "ResidentInboxClassification",
    "ResidentInboxConfig",
    "ResidentInboxRun",
    "ResidentInboxRuntime",
    "ResidentInboxSignal",
    "ResidentInboxStatus",
    "ResidentInboxTriage",
    "classify_inbox_signal",
    "classify_text",
    "parse_inbox_signal",
    "render_inbox_signal",
    "render_inbox_triage",
    "signal_from_directed_message",
    "signal_from_event",
    "signal_from_event_like_record",
]
