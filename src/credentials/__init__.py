"""Credentials domain package."""

from typing import Any

__all__ = ["CredentialsPlugin", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from credentials.app import create_app

        return create_app
    if name == "CredentialsPlugin":
        from credentials.plugin import CredentialsPlugin

        return CredentialsPlugin
    raise AttributeError(name)
