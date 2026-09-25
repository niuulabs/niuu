"""Periodic and on-demand health checking for registered runtime instances.

An instance a operator registers, or that Guild starts up already knowing
about, must never look "idle" when it is actually unreachable. This service
probes every registered instance — on register, on demand, and on a
background interval — and persists the honest result via
``InstanceRepository.record_health``. It never swallows a probe failure into
a healthy-looking default; see ``.claude/rules/no-fallbacks.md``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from niuu.domain.models import InstanceHealthStatus, RegisteredInstance
from niuu.ports.instance_probe import InstanceProbePort, InstanceProbeResult
from niuu.ports.instances import InstanceRepository

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class InstanceHealthCheckResult:
    """The outcome of one probe, and what got persisted for it."""

    probe: InstanceProbeResult
    health: InstanceHealthStatus
    checked_at: datetime
    last_seen_at: datetime | None
    last_error: str | None


class InstanceHealthChecker:
    """Probes registered instances and records reachability.

    ``interval_seconds`` and the probe's own ``timeout_seconds`` are
    configuration (``niuu.health`` — see ``src/niuu/config.py``), never
    hardcoded. ``now`` is injectable so tests can assert exact timestamps
    without depending on wall-clock time.
    """

    def __init__(
        self,
        *,
        repository: InstanceRepository,
        probe: InstanceProbePort,
        interval_seconds: float,
        now: NowFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                "niuu.health.interval_seconds must be > 0 "
                "(the health loop has no meaningful zero/negative interval)"
            )
        self._repository = repository
        self._probe = probe
        self._interval_seconds = interval_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the periodic sweep. Idempotent."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="guild_instance_health_loop")

    async def stop(self) -> None:
        """Stop the periodic sweep and wait for it to finish. Idempotent."""
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        # Sweep once immediately so a fresh registration's health is not
        # merely "unknown" for a full interval after Guild restarts, then on
        # every interval after that.
        while True:
            try:
                await self.check_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Guild instance health sweep failed")
            await self._sleep(self._interval_seconds)

    async def check_all(self) -> list[InstanceHealthCheckResult]:
        """Probe every enabled registered instance and persist each result.

        A disabled instance is excluded from environment selectors already;
        checking it too would just spend a probe on something nothing uses.
        """
        instances = [
            instance for instance in await self._repository.list_instances() if instance.enabled
        ]
        results = await asyncio.gather(
            *(self.check_instance(instance) for instance in instances),
            return_exceptions=True,
        )
        checked: list[InstanceHealthCheckResult] = []
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, BaseException):
                logger.exception(
                    "Health check crashed for instance %s (%s)",
                    instance.id,
                    instance.name,
                    exc_info=result,
                )
                continue
            checked.append(result)
        return checked

    async def check_instance(self, instance: RegisteredInstance) -> InstanceHealthCheckResult:
        """Probe one instance and persist the result. Returns what was persisted.

        ``InstanceProbePort.probe`` is documented to never raise, but this is
        the health checker's own independent net: if a misbehaving or
        third-party probe adapter (``niuu.health.probe.adapter`` is a
        dynamic import — see .claude/rules/dynamic-adapters.md) raises
        anyway, the instance must still be recorded UNREACHABLE rather than
        left however it was on the previous successful check.
        """
        try:
            probe = await self._probe.probe(instance)
        except Exception as exc:
            probe = InstanceProbeResult(
                ok=False, status_code=None, message=str(exc) or exc.__class__.__name__
            )
        checked_at = self._now()
        if probe.ok:
            health = InstanceHealthStatus.OK
            last_seen_at = checked_at
            last_error = None
        else:
            health = InstanceHealthStatus.UNREACHABLE
            # Deliberately carried forward, not cleared: "last seen" answers
            # "when did it last work", which a failed probe does not change.
            last_seen_at = instance.last_seen_at
            last_error = probe.message or "Health probe failed"
        await self._repository.record_health(
            instance.id,
            health=health,
            last_seen_at=last_seen_at,
            last_checked_at=checked_at,
            last_error=last_error,
        )
        return InstanceHealthCheckResult(
            probe=probe,
            health=health,
            checked_at=checked_at,
            last_seen_at=last_seen_at,
            last_error=last_error,
        )
