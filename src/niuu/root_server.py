"""Uvicorn and embedded-database lifecycle for the unified Niuu host."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

import niuu.app as _app
from niuu.app import (
    DEFAULT_HOST_PROFILE,
    SkuldPortRegistry,
    _install_skuld_registry,
    _local_service_host,
    build_root_app,
)
from niuu.config import NiuuSettings
from niuu.ports.plugin import Service
from niuu.service_databases import (
    bootstrap_database,
    database_name_for_service,
    local_service_database_names,
    service_database_env_var,
)

if TYPE_CHECKING:
    from cli.registry import PluginRegistry
    from niuu.ports.embedded_database import EmbeddedDatabasePort

logger = logging.getLogger(__name__)


class RootServer(Service):
    """Single in-process uvicorn server that hosts selected route domains."""

    def __init__(
        self,
        registry: PluginRegistry,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        public_host: str | None = None,
        host_profile: str = DEFAULT_HOST_PROFILE,
        enabled_mounts: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._host = host
        self._public_host = (public_host or host).strip() or host
        self._port = port
        self._host_profile = host_profile
        self._enabled_mounts = enabled_mounts
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._embedded_db: EmbeddedDatabasePort | None = None
        self.skuld_registry = SkuldPortRegistry()
        _install_skuld_registry(self.skuld_registry)

    async def _start_embedded_db(self) -> None:
        """Start embedded PostgreSQL and set env vars for sub-apps."""
        host_config = NiuuSettings().host
        database_mode = host_config.database_mode
        if database_mode == "external":
            logger.info("Skipping embedded PostgreSQL because NIUU_DATABASE_MODE=external")
            return
        if host_config.external_database_host.strip():
            logger.info("Skipping embedded PostgreSQL because DATABASE__HOST is already set")
            return

        from niuu.adapters.embedded_postgres import EmbeddedPostgresDatabase

        data_dir = host_config.pgdata_dir or str(Path.home() / ".niuu" / "pgdata")
        db = EmbeddedPostgresDatabase()
        info = await db.start(data_dir)
        await db.ensure_databases(local_service_database_names())
        self._embedded_db = db

        os.environ["DATABASE__HOST"] = info.host
        os.environ["DATABASE__PORT"] = str(info.port)
        os.environ["DATABASE__USER"] = info.user
        os.environ["DATABASE__PASSWORD"] = ""
        os.environ["DATABASE__NAME"] = database_name_for_service("volundr")
        for service_name in ("volundr", "niuu-shared", "guild", "observatory"):
            os.environ[service_database_env_var(service_name)] = database_name_for_service(
                service_name
            )

        logger.info(
            "Embedded PostgreSQL ready at %s:%s/%s",
            info.host,
            info.port,
            database_name_for_service("volundr"),
        )

    def _build_app(self) -> FastAPI:
        """Compose the root FastAPI app from the selected route domains."""
        return build_root_app(
            registry=self._registry,
            host=self._host,
            public_host=self._public_host,
            port=self._port,
            host_profile=self._host_profile,
            enabled_mounts=self._enabled_mounts,
            skuld_registry=self.skuld_registry,
        )

    async def start(self) -> None:
        await self._start_embedded_db()
        await self._run_migrations()

        os.environ["NIUU_SERVER_HOST"] = self._host
        os.environ["NIUU_SERVER_PUBLIC_HOST"] = self._public_host
        os.environ["NIUU_SERVER_PORT"] = str(self._port)
        os.environ["VOLUNDR__URL"] = f"http://{_local_service_host(self._host)}:{self._port}"

        app = self._build_app()
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

    async def _run_migrations(self) -> None:
        """Run database migrations for all services."""
        if self._embedded_db is None:
            return
        try:
            import asyncpg

            from cli.resources import migration_dir, ordered_migration_files

            info = self._embedded_db._connection_info
            volundr_conn = await asyncpg.connect(
                host=info.host,
                port=info.port,
                user=info.user,
                database=database_name_for_service("volundr"),
            )
            try:
                for variant in ("volundr", "ting"):
                    try:
                        mig_dir = migration_dir(variant)
                    except FileNotFoundError:
                        logger.debug("No migrations found for %s", variant)
                        continue
                    sql_files = ordered_migration_files(mig_dir)
                    applied = 0
                    for sql_file in sql_files:
                        sql = sql_file.read_text()
                        try:
                            await volundr_conn.execute(sql)
                            applied += 1
                        except Exception:
                            logger.debug("Migration %s skipped: %s", sql_file.name, exc_info=True)
                    logger.info("Applied %d/%d %s migrations", applied, len(sql_files), variant)
            finally:
                await volundr_conn.close()

            for service_name in ("niuu-shared", "guild", "observatory"):
                bootstrap_sql = _app.bootstrap_sql_for_service(service_name)
                if not bootstrap_sql:
                    continue
                await bootstrap_database(
                    host=info.host,
                    port=info.port,
                    user=info.user,
                    password="",
                    database=database_name_for_service(service_name),
                    statements=bootstrap_sql,
                )
                logger.info(
                    "Bootstrapped local %s database %s",
                    service_name,
                    database_name_for_service(service_name),
                )
        except Exception:
            logger.exception("Failed to run migrations")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
            self._task = None
        if self._embedded_db:
            await self._embedded_db.stop()
            self._embedded_db = None

    async def health_check(self) -> bool:
        return self._server is not None and self._server.started
