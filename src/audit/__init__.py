"""Audit domain package."""

from typing import Any

__all__ = ["AuditPlugin", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from audit.app import create_app

        return create_app
    if name == "AuditPlugin":
        from audit.plugin import AuditPlugin

        return AuditPlugin
    raise AttributeError(name)
