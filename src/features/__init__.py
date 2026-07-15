"""Features domain package."""

from typing import Any

__all__ = ["FeaturesPlugin", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from features.app import create_app

        return create_app
    if name == "FeaturesPlugin":
        from features.plugin import FeaturesPlugin

        return FeaturesPlugin
    raise AttributeError(name)
