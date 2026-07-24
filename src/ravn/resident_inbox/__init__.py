"""Resident inbox intake: classify and store directed messages and events."""

from __future__ import annotations

from .backend import LocalResidentInbox, MimirResidentInbox
from .classify import classify_text
from .models import (
    ResidentInboxBackend,
    ResidentInboxClassification,
    ResidentInboxConfig,
    ResidentInboxRun,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .serialization import (
    parse_inbox_signal,
    render_inbox_signal,
    render_inbox_triage,
    signal_from_directed_message,
    signal_from_event,
)

__all__ = [
    "MimirResidentInbox",
    "LocalResidentInbox",
    "ResidentInboxBackend",
    "ResidentInboxClassification",
    "ResidentInboxConfig",
    "ResidentInboxRun",
    "ResidentInboxSignal",
    "ResidentInboxStatus",
    "ResidentInboxTriage",
    "classify_text",
    "parse_inbox_signal",
    "render_inbox_signal",
    "render_inbox_triage",
    "signal_from_directed_message",
    "signal_from_event",
]
