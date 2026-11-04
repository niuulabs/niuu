"""A resident republishes its identity, with or without signal sources."""

from __future__ import annotations

import asyncio

from ravn.config import EnvironmentConfig, Settings
from ravn.environment_signal_runtime import build_runtime_environment
from ravn.resident_identity import ResidentIdentityPublisher
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent


def _settings() -> Settings:
    settings = Settings(
        environment=EnvironmentConfig(
            id="env-k8s-niuu",
            name="Niuu",
            type="kubernetes",
            resident_name="Regin",
            charter="Steward the delivery of the platform's own tracker work.",
            signal_sources=[],
            runtime_snapshot_interval_seconds=0.01,
        )
    )
    settings.mesh.own_peer_id = "regin"
    http = settings.gateway.channels.http
    http.enabled = True
    http.public_url = "https://regin.valhalla.test"
    http.mimir_served_mount = "local"
    return settings


async def _collect(settings: Settings, publisher) -> list[SleipnirEvent]:
    events: list[SleipnirEvent] = []

    async def record(event: SleipnirEvent) -> None:
        events.append(event)

    bus = InProcessBus()
    await bus.subscribe(["valkyrie.runtime.started"], record)
    identity = ResidentIdentityPublisher(
        settings=settings,
        publisher=publisher or bus,
        environment=build_runtime_environment(settings),
    )
    await identity.start()
    await asyncio.sleep(0.05)
    await identity.stop()
    await bus.flush()
    return events


async def test_a_resident_without_signal_sources_still_reports_its_identity() -> None:
    events = await _collect(_settings(), None)

    assert len(events) >= 3
    payload = events[0].payload
    assert payload["valkyrie_id"] == "regin"
    assert payload["charter"] == "Steward the delivery of the platform's own tracker work."
    assert payload["memory"] == [
        {
            "name": "local",
            "role": "local",
            "platform_mount": "resident-regin",
            "served_url": "https://regin.valhalla.test",
        }
    ]
    assert payload["nats_subject"].endswith("valkyrie.runtime.started")


async def test_a_failed_publish_is_counted_and_retried(monkeypatch) -> None:
    degraded: list[str] = []
    monkeypatch.setattr(
        "ravn.resident_identity.record_degraded", lambda path, message: degraded.append(path)
    )

    class FlakyPublisher:
        calls = 0

        async def publish(self, event: SleipnirEvent) -> None:
            FlakyPublisher.calls += 1
            if FlakyPublisher.calls == 1:
                raise ConnectionError("nats down")

    await _collect(_settings(), FlakyPublisher())

    assert degraded == ["resident_identity.publish"]
    assert FlakyPublisher.calls >= 2


async def test_a_resident_without_mimir_reports_no_memory() -> None:
    settings = _settings()
    settings.mimir.enabled = False

    events = await _collect(settings, None)

    assert events[0].payload["memory"] == []
