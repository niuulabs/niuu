"""Retention: run and session layers are deleted a while after their run or session ends.

Design section 4 (ephemeral layers) and decision 1 of section 13: a run or
session layer lives ``memory.retention_days`` after its run or session ended,
then it is deleted. Every ``memory.retention_sweep_seconds`` the sweeper lists
each memory system's namespaces, asks Forge (contract C7, as the gateway's own
service) which of their sessions and runs are active, ended (and when) or gone,
and deletes the layers whose retention has passed and those Forge no longer
knows, with their proposal namespaces.

A sweep that cannot ask Forge or list a system deletes nothing on that system;
it is logged at error, counted (``memory.retention_sweep`` on the degraded
counter), and tried again at the next interval, which is the bound on retrying.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from mimir.memory.models import EPHEMERAL_LAYER_KINDS, Layer, LayerKind, MemoryStoreError
from mimir.memory.systems import MemorySystems
from mimir.ports.layer_lifetimes import (
    LayerLifetimes,
    LifeState,
    Lifetime,
    LifetimesUnavailableError,
)
from mimir.ports.memory_backend import MemoryBackend
from niuu.cap_telemetry import record_degraded
from niuu.domain.mimir_facts import FactBackendError

logger = logging.getLogger(__name__)

SWEEP_PATH = "memory.retention_sweep"
_HELD_UNDER = ("_proposals/", "_decided/")


class SweepFailedError(Exception):
    """A system could not be swept; nothing was deleted on it."""


@dataclass
class SweepReport:
    system: str
    deleted: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _layer_of(namespace: str) -> Layer | None:
    """The ephemeral layer a namespace belongs to (its own, or its proposals'), else None."""
    base = namespace
    for prefix in _HELD_UNDER:
        if namespace.startswith(prefix):
            base = namespace[len(prefix) :]
    kind, _, key = base.partition("-")
    if kind not in {k.value for k in EPHEMERAL_LAYER_KINDS}:
        return None
    try:
        return Layer(LayerKind(kind), key)
    except MemoryStoreError:
        return None


class RetentionSweeper:
    """Args:
    systems: every memory system to sweep.
    lifetimes: Forge's answer on which sessions and runs ended (C7).
    retention_days: how long a layer outlives its session or run.
    interval_seconds: time between sweeps.
    forget: told of each deleted namespace (system, namespace), so the gateway
        prepares it again should its session come back.
    clock: now, for tests.
    """

    def __init__(
        self,
        systems: MemorySystems,
        lifetimes: LayerLifetimes,
        *,
        retention_days: float,
        interval_seconds: float,
        forget: Callable[[str, str], None],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention_days < 0:
            raise ValueError("memory.retention_days must not be negative")
        if interval_seconds <= 0:
            raise ValueError("memory.retention_sweep_seconds must be positive")
        self._systems = systems
        self._lifetimes = lifetimes
        self._retention = timedelta(days=retention_days)
        self._interval = float(interval_seconds)
        self._forget = forget
        self._clock = clock or (lambda: datetime.now(UTC))
        self.deleted_total = 0
        self.failed_sweeps = 0

    async def close(self) -> None:
        await self._lifetimes.close()

    async def run(self) -> None:
        """Sweep now and then every interval, until cancelled. A failed sweep is counted."""
        while True:
            try:
                await self.sweep()
            except Exception as exc:  # the loop must outlive one bad sweep; counted, retried
                self.failed_sweeps += 1
                record_degraded(
                    SWEEP_PATH,
                    f"the retention sweep failed ({type(exc).__name__}: {exc}); "
                    f"retrying in {self._interval:g} s",
                )
            await asyncio.sleep(self._interval)

    async def sweep(self) -> list[SweepReport]:
        """One pass over every system. A system that fails is counted and skipped."""
        reports = []
        for name, backend in self._systems.items():
            try:
                reports.append(await self.sweep_system(name, backend))
            except SweepFailedError as exc:
                self.failed_sweeps += 1
                record_degraded(SWEEP_PATH, f"{exc}; retrying in {self._interval:g} s")
        return reports

    async def sweep_system(self, name: str, backend: MemoryBackend) -> SweepReport:
        try:
            namespaces = await backend.list_namespaces()
        except (MemoryStoreError, FactBackendError) as exc:
            raise SweepFailedError(
                f"memory system {name!r}: its namespaces could not be listed: {exc}"
            ) from exc
        layers: dict[Layer, list[str]] = {}
        for namespace in namespaces:
            layer = _layer_of(namespace)
            if layer is not None:
                layers.setdefault(layer, []).append(namespace)
        report = SweepReport(system=name)
        if not layers:
            return report
        sessions = sorted({lyr.key for lyr in layers if lyr.kind is LayerKind.SESSION})
        runs = sorted({lyr.key for lyr in layers if lyr.kind is LayerKind.RUN})
        try:
            lifetimes = await self._lifetimes.ended(sessions, runs)
        except LifetimesUnavailableError as exc:
            raise SweepFailedError(f"memory system {name!r}: {exc}") from exc
        now = self._clock()
        for layer, held in layers.items():
            known = lifetimes.sessions if layer.kind is LayerKind.SESSION else lifetimes.runs
            if not self._expired(known[layer.key], now):
                report.kept.append(layer.id)
                continue
            for namespace in held:
                try:
                    removal = await backend.delete_namespace(namespace)
                except (MemoryStoreError, FactBackendError) as exc:
                    report.failed.append(namespace)
                    record_degraded(
                        SWEEP_PATH,
                        f"memory system {name!r}: namespace {namespace} of {layer.id} could not "
                        f"be deleted ({exc}); retrying in {self._interval:g} s",
                    )
                    continue
                self._forget(name, namespace)
                self.deleted_total += 1
                report.deleted.append(namespace)
                logger.info(
                    "memory retention: deleted %s on %s (%d pages, %d facts withdrawn)",
                    namespace,
                    name,
                    removal.pages_removed,
                    removal.facts_withdrawn,
                )
        logger.info(
            "memory retention on %s: %d namespace(s) deleted, %d layer(s) kept, %d failed",
            name,
            len(report.deleted),
            len(report.kept),
            len(report.failed),
        )
        return report

    def _expired(self, lifetime: Lifetime, now: datetime) -> bool:
        if lifetime.state is LifeState.GONE:
            return True
        if lifetime.state is LifeState.ACTIVE or lifetime.ended_at is None:
            return False
        return lifetime.ended_at + self._retention <= now
