"""Integrations domain package."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.app import create_app
    from integrations.plugin import IntegrationsPlugin

__all__ = ["IntegrationsPlugin", "create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from integrations.app import create_app

        return create_app
    if name == "IntegrationsPlugin":
        from integrations.plugin import IntegrationsPlugin

        return IntegrationsPlugin
    raise AttributeError(name)
