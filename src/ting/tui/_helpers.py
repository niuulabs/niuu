"""Shared formatting helpers for Ting TUI pages."""

from __future__ import annotations

from typing import Any


def format_confidence(value: float | Any) -> str:
    """Format a confidence value as a percentage string."""
    if isinstance(value, float):
        return f"{value * 100:.0f}%"
    return str(value)
