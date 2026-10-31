"""Compatibility re-export: the Telegram adapter moved to niuu.

``integration_connections.adapter`` stores
``ting.adapters.telegram_notification.TelegramNotificationAdapter``; this module
keeps that class path resolving to the shared implementation.
"""

from niuu.adapters.notifications.telegram import TELEGRAM_API, TelegramNotificationAdapter

__all__ = ["TELEGRAM_API", "TelegramNotificationAdapter"]
