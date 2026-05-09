"""Credentials domain package."""

from credentials.app import create_app
from credentials.plugin import CredentialsPlugin

__all__ = ["CredentialsPlugin", "create_app"]
