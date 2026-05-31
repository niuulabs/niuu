"""Shared database configuration and pool helpers for Niuu service apps."""

from __future__ import annotations

from niuu.service_databases import database_pool
from volundr.config import DatabaseConfig

__all__ = ["DatabaseConfig", "database_pool"]
