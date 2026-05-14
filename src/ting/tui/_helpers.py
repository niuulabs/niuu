"""Shared formatting helpers for Ting TUI pages."""

from __future__ import annotations

from typing import Any


def format_confidence(value: float | Any) -> str:
    """Format a confidence value as a percentage string."""
    if isinstance(value, float):
        return f"{value * 100:.0f}%"
    return str(value)


def format_confidence_history(
    history: list[dict[str, Any]],
    muted_color: str,
) -> str:
    """Format the last 5 confidence history deltas as a Rich markup string."""
    if not history:
        return ""
    deltas = [f"{e.get('delta', 0):+.0%}" for e in history[-5:]]
    return f"  [{muted_color}]delta {' '.join(deltas)}[/]"
