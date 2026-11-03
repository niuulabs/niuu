"""Composition for artifact publications (F1), burst notices (F1n) and host identity (F7).

Kept out of ``main.py`` so the composition root only calls three functions: build the
service, mount its routers, and run the durable hold beside the other background loops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from volundr.adapters.outbound.postgres_artifacts import PostgresArtifactPublicationRepository
from volundr.domain.artifacts import BurstPolicy, TypeCatalog
from volundr.domain.services.artifacts import ArtifactService

if TYPE_CHECKING:
    from volundr.config import Settings
    from volundr.domain.services.notifications import NotificationService
    from volundr.domain.services.session import SessionService

logger = logging.getLogger(__name__)

FALLBACK_HOST = "local"


def configured_host(settings: Settings) -> str | None:
    """This Forge's configured host name: ``host_identity.host``, else
    ``projects.instance_id``; ``None`` when neither is set. The session status card
    reports it as ``host``."""
    return settings.host_identity.host.strip() or settings.projects.instance_id.strip() or None


def resolve_host(settings: Settings) -> str:
    """The host key publications and bursts are stored under: the configured host, or
    ``local`` on a Forge that names none (a key, never shown as a measured host)."""
    return configured_host(settings) or FALLBACK_HOST


def resolve_tailnet_name(settings: Settings) -> str:
    return settings.host_identity.tailnet_name.strip() or settings.server_public_host


def burst_policy(settings: Settings) -> BurstPolicy:
    config = settings.artifacts
    return BurstPolicy(
        window=timedelta(seconds=config.burst_window_seconds),
        hold=timedelta(seconds=config.hold_seconds),
        live_lag=timedelta(seconds=config.live_lag_seconds),
    )


def create_artifact_service(
    settings: Settings,
    pool: Any,
    *,
    notification_service: NotificationService | None,
    session_service: SessionService,
) -> ArtifactService | None:
    config = settings.artifacts
    if not config.enabled:
        logger.info("Artifact publications disabled (artifacts.enabled=false)")
        return None
    notices = notification_service if config.notices_enabled else None
    if notification_service is None and config.notices_enabled:
        logger.info("Burst notices need notifications; publications are recorded silently")
    return ArtifactService(
        PostgresArtifactPublicationRepository(pool),
        host=resolve_host(settings),
        policy=burst_policy(settings),
        catalog=TypeCatalog.from_config(config.type_urls, config.type_names),
        notices=notices,
        session_lookup=session_service.get_session,
        emit_batch_size=config.emit_batch_size,
        backfill_page_size=config.backfill_page_size,
        max_emit_failures=config.max_emit_failures,
    )


class ArtifactHold:
    """Runs :meth:`ArtifactService.emit_due` every tick. Restart-safe by construction:
    due times and outcomes are persisted, and each emission rechecks in its transaction."""

    def __init__(
        self,
        service: ArtifactService,
        *,
        interval_seconds: float,
        before_first_tick: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._before_first_tick = before_first_tick
        self._task: asyncio.Task | None = None

    async def tick(self, now: datetime | None = None) -> int:
        try:
            results = await self._service.emit_due(now or datetime.now(UTC))
        except Exception:
            logger.exception("artifact hold tick failed; retrying next tick")
            return 0
        return len(results)

    async def _run(self) -> None:
        if self._before_first_tick is not None:
            await self._before_first_tick()
        while True:
            await self.tick()
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


async def startup_backfill(service: ArtifactService, settings: Settings) -> None:
    """Optional silent replay at start (``artifacts.backfill_on_start``)."""
    config = settings.artifacts
    if not config.backfill_on_start:
        return
    since = None
    if config.backfill_since_days is not None:
        since = datetime.now(UTC) - timedelta(days=config.backfill_since_days)
    try:
        report = await service.backfill(since=since)
    except Exception:
        logger.exception("artifact start-up backfill failed")
        return
    logger.info(
        "artifact backfill: %d rows scanned, %d publications recorded",
        report.scanned_rows,
        report.recorded,
    )
