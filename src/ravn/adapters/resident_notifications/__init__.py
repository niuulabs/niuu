"""Adapters for :class:`ravn.ports.resident_notifications.ResidentNotificationOutboxPort`."""

from __future__ import annotations

from ravn.adapters.resident_notifications.sqlite import SqliteResidentNotificationOutbox

__all__ = ["SqliteResidentNotificationOutbox"]
