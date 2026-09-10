"""Port interface for embedded database lifecycle management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectionInfo:
    """Connection parameters for an embedded database instance."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = ""


class EmbeddedDatabasePort(ABC):
    """Port for managing an embedded PostgreSQL instance.

    Implementations handle the full lifecycle: start, connect, query, stop.
    Used by the CLI to provide a zero-config local database.
    """

    @abstractmethod
    async def start(self, data_dir: str) -> ConnectionInfo:
        """Start the embedded database, returning connection info.

        Args:
            data_dir: Directory for PostgreSQL data files.

        Returns:
            ConnectionInfo with host/port/dbname/user for connecting.
        """

    @abstractmethod
    async def execute(self, sql: str, *args: object) -> list[dict]:
        """Execute a SQL statement and return rows as dicts.

        Args:
            sql: SQL query with $1, $2... placeholders.
            *args: Positional parameters for the query.

        Returns:
            List of row dicts for SELECT; empty list for non-SELECT.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the embedded database and release resources."""

    @abstractmethod
    async def is_running(self) -> bool:
        """Check whether the embedded database is accepting connections."""
