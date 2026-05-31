"""Shared service settings surface for co-hosted Niuu services."""

from __future__ import annotations

from niuu.service_database import DatabaseConfig
from volundr.config import OAuthConfig, Settings

__all__ = ["DatabaseConfig", "OAuthConfig", "Settings"]
