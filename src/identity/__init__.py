"""Identity domain package."""

from typing import Any

__all__ = ["IdentityPlugin", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from identity.app import create_app

        return create_app
    if name == "IdentityPlugin":
        from identity.plugin import IdentityPlugin

        return IdentityPlugin
    raise AttributeError(name)
