"""Compatibility re-export: the channel port moved to :mod:`niuu.ports.notification_channel`."""

from niuu.ports.notification_channel import (
    Notification,
    NotificationChannel,
    NotificationUrgency,
)

__all__ = ["Notification", "NotificationChannel", "NotificationUrgency"]
