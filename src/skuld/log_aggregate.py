"""Backward-compatible re-export for workspace log aggregation helpers."""

from volundr.log_aggregate import (
    AggregateLogEntry,
    AggregateParticipant,
    ParticipantKind,
    aggregate_workspace_logs,
)

__all__ = [
    "AggregateLogEntry",
    "AggregateParticipant",
    "ParticipantKind",
    "aggregate_workspace_logs",
]
