"""Resident inbox intake: classify and store directed messages and events."""

from __future__ import annotations

from .archive import RawSignalArchive, archive_ref_sort_key
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
from .shape import ShapeAggregate, aggregate_summary_lines, field_paths, shape_key

__all__ = [
    "MimirResidentInbox",
    "LocalResidentInbox",
    "RawSignalArchive",
    "ShapeAggregate",
    "aggregate_summary_lines",
    "archive_ref_sort_key",
    "field_paths",
    "shape_key",
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
