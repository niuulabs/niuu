"""Integrations domain package."""

from integrations.app import create_app
from integrations.plugin import IntegrationsPlugin

__all__ = ["IntegrationsPlugin", "create_app"]
