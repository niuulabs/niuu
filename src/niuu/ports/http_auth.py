"""Ports for outbound HTTP auth/header construction."""

from __future__ import annotations

from typing import Protocol


class HttpAuthPort(Protocol):
    """Build auth headers for outbound HTTP requests."""

    def headers(self) -> dict[str, str]:
        """Return HTTP headers to merge into an outbound request."""

    def invalidate(self) -> bool:
        """Discard a refreshable credential after an authentication rejection."""
