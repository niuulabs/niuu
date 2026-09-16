"""Compatibility export for the shared, transactional PostgreSQL schema runner."""

from niuu.adapters.postgres_schema import (
    LEDGER_SQL,
    SCHEMA_LOCK,
    apply_startup_migrations,
)

__all__ = ["LEDGER_SQL", "SCHEMA_LOCK", "apply_startup_migrations"]
