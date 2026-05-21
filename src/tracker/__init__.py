"""Tracker domain package."""

from tracker.app import create_app
from tracker.plugin import TrackerPlugin

__all__ = ["TrackerPlugin", "create_app"]
