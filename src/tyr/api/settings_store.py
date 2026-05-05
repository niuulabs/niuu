"""Shared in-process storage for Tyr operator settings routes.

These settings are intentionally mounted behind ``/api/v1/tyr/settings/*`` and
kept distinct from runtime workflow config like ``/api/v1/tyr/flock/config``.
Until Tyr grows a dedicated settings repository, we persist changes in
``app.state`` for the lifetime of the process and seed initial values from the
loaded ``Settings`` object.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from tyr.config import Settings


def now_utc() -> datetime:
    """Return a timezone-aware timestamp for settings updates."""
    return datetime.now(UTC)


def _default_notification_channel(settings: Settings) -> str:
    """Return a frontend-valid default channel for the current deployment."""
    return "telegram" if settings.notification.enabled else "none"


def _seed_settings_store(settings: Settings) -> dict[str, dict[str, Any]]:
    """Build the initial in-process settings store from application config."""
    return {
        "flock": {
            "flock_name": "default",
            "default_base_branch": "main",
            "default_tracker_type": "linear",
            "default_repos": [],
            "max_active_sagas": 10,
            "auto_create_milestones": True,
            "updated_at": now_utc(),
        },
        "dispatch": {
            "confidence_threshold": float(settings.notification.confidence_threshold),
            "max_concurrent_raids": 3,
            "auto_continue": False,
            "batch_size": int(settings.watcher.batch_size),
            "retry_policy": {
                "max_retries": 3,
                "retry_delay_seconds": 30,
                "escalate_on_exhaustion": True,
            },
            "quiet_hours": "22:00-07:00 UTC",
            "escalate_after": "30m",
            "updated_at": now_utc(),
        },
        "notifications": {
            "channel": _default_notification_channel(settings),
            "on_raid_pending_approval": bool(settings.notification.enabled),
            "on_raid_merged": bool(settings.notification.enabled),
            "on_raid_failed": bool(settings.notification.enabled),
            "on_saga_complete": bool(settings.notification.enabled),
            "on_dispatcher_error": bool(settings.notification.enabled),
            "webhook_url": None,
            "updated_at": now_utc(),
        },
    }


def get_tyr_settings_store(request: Request) -> dict[str, dict[str, Any]]:
    """Return the process-local Tyr settings store, seeding it once if needed."""
    existing = getattr(request.app.state, "tyr_http_settings", None)
    if existing is None:
        existing = _seed_settings_store(request.app.state.settings)
        request.app.state.tyr_http_settings = existing
    return existing
