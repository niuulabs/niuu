"""Identity domain package."""

from identity.app import create_app
from identity.plugin import IdentityPlugin

__all__ = ["IdentityPlugin", "create_app"]
