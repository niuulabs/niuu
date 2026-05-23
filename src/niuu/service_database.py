"""Shared database configuration and pool helpers for Niuu service apps."""

from __future__ import annotations

from volundr.config import DatabaseConfig

from niuu.service_databases import database_pool

__all__ = ["DatabaseConfig", "database_pool"]
