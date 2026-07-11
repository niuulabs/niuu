"""Installed backend package version."""

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    """Return the installed distribution version or a checkout marker."""
    try:
        return version("volundr")
    except PackageNotFoundError:
        return "0.0.0-dev"
