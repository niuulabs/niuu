"""A resident's runtime identity, published for readers that cannot replay it.

The dashboard projection and the platform's Mímir learn a resident's charter,
personality and memory from ``valkyrie.runtime.started``. Nothing replays that
event, so it is republished on an interval, and by every resident: one with no
signal sources builds no signal runtime but still has an identity to report.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from ravn.cap_telemetry import record_degraded
from ravn.config import Settings
from ravn.domain.environment import Environment, apply_environment_metadata
from sleipnir.domain.events import SleipnirEvent

RUNTIME_STARTED_EVENT = "valkyrie.runtime.started"


def resident_name(settings: Settings) -> str:
    return settings.environment.resident_name or settings.mesh.own_peer_id or "Valkyrie"


def resident_memory(settings: Settings) -> list[dict[str, str]]:
    """Where this resident keeps its memory, for a workspace to open it.

    A store without ``mimir.instances`` is the single local store the write
    routing already calls ``local``. The store the gateway serves is reported
    at the gateway's public URL, under ``resident-<id>`` unless the operator
    named its platform mount.
    """
    mimir = settings.mimir
    if not mimir.enabled:
        return []
    stores = [
        (instance.name, instance.role, instance.platform_mount.strip())
        for instance in mimir.instances
    ] or [("local", "local", "")]
    http = settings.gateway.channels.http
    served = http.mimir_served_mount.strip() if http.enabled else ""
    return [
        {
            "name": name,
            "role": role,
            "platform_mount": (
                (platform_mount or f"resident-{settings.mesh.own_peer_id}")
                if name == served
                else platform_mount
            ),
            "served_url": http.public_url.strip().rstrip("/") if name == served else "",
        }
        for name, role, platform_mount in stores
    ]


def resident_identity_payload(settings: Settings, environment: Environment) -> dict[str, Any]:
    """What a resident is, independent of which runtimes it runs."""
    return {
        "valkyrie_id": settings.mesh.own_peer_id,
        "valkyrie_name": resident_name(settings),
        "resident_personality": settings.environment.resident_personality.strip(),
        "charter": settings.environment.charter.strip(),
        "memory": resident_memory(settings),
        "environment_id": environment.id,
        "signal_task_severities": list(settings.environment.signal_task_severities),
        "initiative_enabled": settings.initiative.enabled,
        "llm_model": settings.effective_model(),
        "reflection_model": settings.effective_memory_reflection_model(),
        "post_session_reflection_enabled": settings.reflection.enabled,
        "post_session_reflection_model": (
            settings.effective_post_session_reflection_config().llm_alias
        ),
    }


class ResidentIdentityPublisher:
    """Republish the resident's identity every ``runtime_snapshot_interval_seconds``."""

    def __init__(self, *, settings: Settings, publisher: Any, environment: Environment) -> None:
        self._settings = settings
        self._publisher = publisher
        self._environment = environment
        self._interval = settings.environment.runtime_snapshot_interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="resident_identity")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def publish(self) -> None:
        event = SleipnirEvent(
            event_type=RUNTIME_STARTED_EVENT,
            source=f"ravn:valkyrie:{self._environment.id}",
            payload=resident_identity_payload(self._settings, self._environment),
            summary=f"Valkyrie {resident_name(self._settings)} identity in {self._environment.id}",
            urgency=0.1,
            domain="infrastructure",
            timestamp=SleipnirEvent.now(),
            tenant_id=self._environment.tenant_id,
        )
        await self._publisher.publish(apply_environment_metadata(event, self._environment))

    async def _run(self) -> None:
        while True:
            try:
                await self.publish()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The next snapshot retries; stopping would leave readers that
                # restarted without this resident's identity for good.
                record_degraded(
                    "resident_identity.publish",
                    f"could not publish {RUNTIME_STARTED_EVENT}: {type(exc).__name__}: {exc}",
                )
            await asyncio.sleep(self._interval)
