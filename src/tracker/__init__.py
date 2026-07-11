"""Tracker domain package."""

from typing import Any

__all__ = ["TrackerPlugin", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from tracker.app import create_app

        return create_app
    if name == "TrackerPlugin":
        from tracker.plugin import TrackerPlugin

        return TrackerPlugin
    raise AttributeError(name)
