"""Composition of artifact publications (F1), the durable hold (F1n) and host identity (F7)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.postgres_artifacts import PostgresArtifactPublicationRepository
from volundr.artifact_runtime import (
    ArtifactHold,
    burst_policy,
    configured_host,
    create_artifact_service,
    resolve_host,
    resolve_tailnet_name,
    startup_backfill,
)
from volundr.config import ArtifactsConfig, HostIdentityConfig, ProjectsConfig


def _settings(
    *, host: str = "", instance_id: str = "", tailnet: str = "", **artifacts
) -> SimpleNamespace:
    return SimpleNamespace(
        host_identity=HostIdentityConfig(host=host, tailnet_name=tailnet),
        projects=ProjectsConfig(instance_id=instance_id),
        server_public_host="100.66.123.128",
        artifacts=ArtifactsConfig(**artifacts),
    )


@pytest.mark.parametrize(
    "host,instance_id,expected",
    [("thor", "thor-exp", "thor"), ("", "thor-exp", "thor-exp"), ("", " ", "local")],
)
def test_resolve_host_precedence(host, instance_id, expected):
    assert resolve_host(_settings(host=host, instance_id=instance_id)) == expected


@pytest.mark.parametrize(
    "host,instance_id,expected",
    [("thor", "thor-exp", "thor"), ("", "thor-exp", "thor-exp"), ("", " ", None)],
)
def test_status_host_is_the_configured_host_or_unknown(host, instance_id, expected):
    # The session status card reports this; an unnamed Forge is unknown, never "local".
    assert configured_host(_settings(host=host, instance_id=instance_id)) == expected


def test_resolve_tailnet_name_falls_back_to_the_public_host():
    assert resolve_tailnet_name(_settings(tailnet="thor.ts.net")) == "thor.ts.net"
    assert resolve_tailnet_name(_settings()) == "100.66.123.128"


def test_burst_policy_from_settings():
    policy = burst_policy(_settings(burst_window_seconds=60, hold_seconds=5, live_lag_seconds=30))
    assert policy.window == timedelta(seconds=60)
    assert policy.hold == timedelta(seconds=5)
    assert policy.live_lag == timedelta(seconds=30)


def test_create_artifact_service_disabled_is_none():
    sessions = MagicMock()
    assert (
        create_artifact_service(
            _settings(enabled=False),
            MagicMock(),
            notification_service=None,
            session_service=sessions,
        )
        is None
    )


def test_create_artifact_service_wires_notices_and_host():
    notifications, sessions = MagicMock(), MagicMock()
    service = create_artifact_service(
        _settings(instance_id="thor-exp"),
        MagicMock(),
        notification_service=notifications,
        session_service=sessions,
    )
    assert service is not None
    assert service.host == "thor-exp"
    assert service._notices is notifications
    assert service._session_lookup is sessions.get_session
    assert isinstance(service._repository, PostgresArtifactPublicationRepository)


def test_create_artifact_service_with_notices_off_records_silently(caplog):
    service = create_artifact_service(
        _settings(notices_enabled=False),
        MagicMock(),
        notification_service=MagicMock(),
        session_service=MagicMock(),
    )
    assert service is not None and service._notices is None
    with caplog.at_level(logging.INFO):
        quiet = create_artifact_service(
            _settings(), MagicMock(), notification_service=None, session_service=MagicMock()
        )
    assert quiet is not None and quiet._notices is None
    assert "recorded silently" in caplog.text


async def test_hold_tick_counts_results_and_swallows_failures(caplog):
    service = MagicMock()
    service.emit_due = AsyncMock(return_value=["a", "b"])
    hold = ArtifactHold(service, interval_seconds=1)
    now = datetime(2026, 10, 22, tzinfo=UTC)
    assert await hold.tick(now) == 2
    service.emit_due.assert_awaited_with(now)
    assert await hold.tick() == 2  # defaults to the wall clock

    service.emit_due = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        assert await hold.tick(now) == 0
    assert "artifact hold tick failed" in caplog.text


async def test_hold_runs_before_first_tick_once_and_stops_idempotently():
    ticked = asyncio.Event()
    service = MagicMock()

    async def emit(now):
        ticked.set()
        return []

    service.emit_due = emit
    before = AsyncMock()
    hold = ArtifactHold(service, interval_seconds=0.01, before_first_tick=before)
    await hold.stop()  # never started: a no-op
    hold.start()
    hold.start()  # already running: keeps the one task
    await asyncio.wait_for(ticked.wait(), 1)
    await asyncio.sleep(0.05)
    await hold.stop()
    await hold.stop()
    before.assert_awaited_once()


async def test_startup_backfill_off_does_nothing():
    service = MagicMock()
    service.backfill = AsyncMock()
    await startup_backfill(service, _settings())
    service.backfill.assert_not_awaited()


async def test_startup_backfill_bounded_by_days(caplog):
    service = MagicMock()
    service.backfill = AsyncMock(return_value=SimpleNamespace(scanned_rows=9, recorded=4))
    with caplog.at_level(logging.INFO):
        await startup_backfill(service, _settings(backfill_on_start=True, backfill_since_days=2))
    since = service.backfill.await_args.kwargs["since"]
    assert (
        timedelta(days=2) - timedelta(minutes=1)
        < datetime.now(UTC) - since
        < timedelta(days=2, minutes=1)
    )
    assert "9 rows scanned, 4 publications recorded" in caplog.text

    service.backfill = AsyncMock(return_value=SimpleNamespace(scanned_rows=0, recorded=0))
    await startup_backfill(service, _settings(backfill_on_start=True))
    assert service.backfill.await_args.kwargs["since"] is None


async def test_startup_backfill_failure_is_logged(caplog):
    service = MagicMock()
    service.backfill = AsyncMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR):
        await startup_backfill(service, _settings(backfill_on_start=True))
    assert "start-up backfill failed" in caplog.text
