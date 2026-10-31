"""Compatibility re-export: the webhook adapter moved to niuu.

``integration_connections.adapter`` stores
``ting.adapters.webhook_notification.WebhookNotificationAdapter``; this module
keeps that class path resolving to the shared implementation.
"""

from niuu.adapters.notifications.webhook import WebhookNotificationAdapter

__all__ = ["WebhookNotificationAdapter"]
