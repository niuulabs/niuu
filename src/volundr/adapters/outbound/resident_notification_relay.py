"""Relay residents' notifications into the Forge notification feed.

A resident (a long-lived Ravn) is not a Forge session: it has no session log or
activity reports for Forge to project notifications from. Instead it keeps a
durable notification outbox of its own (:mod:`niuu.domain.resident_notifications`):
the questions it waits on its owner for, when those are answered, and what its
agent notifies on purpose. Its OpenClaw shim serves that outbox.

This relay holds one connection per active resident whose session controller can
read it (``ResidentNotificationSource``), the same per-resident, credential-store
token connection Forge already drives the resident's channels with, so it works
on every resident backend with nothing to configure. It records each entry through
the notification service (the same feed, outbox and delivery rules as a session's
notifications), then acknowledges it so the resident can drop it.

Every recording is idempotent (dedupe keys), so a replay after a reconnect, or two
Forge replicas draining the same resident, never yields a second notification.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from niuu.domain.resident_notifications import (
    AttentionInputKind,
    ResidentNotificationEntry,
    ResidentNotificationOp,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDesiredState,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.notifications import NotificationValidationError
from volundr.domain.ports import (
    ResidentNotificationSource,
    ResidentNotificationStream,
    ResidentNotificationsUnavailableError,
    ResidentRuntimeRepository,
    ResidentSessionController,
)
from volundr.domain.services.notifications import NotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelaySettings:
    """How the relay paces itself (``resident_runtimes.notifications``)."""

    batch_size: int
    idle_resync_seconds: float
    reconnect_initial_seconds: float
    reconnect_max_seconds: float

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if self.idle_resync_seconds <= 0 or self.reconnect_initial_seconds <= 0:
            raise ValueError("relay intervals must be positive")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("reconnect_max_seconds must be at least reconnect_initial_seconds")


class ResidentNotificationRelay:
    """Follow every active resident's notification outbox into the Forge feed."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        session_controllers: list[ResidentSessionController],
        notifications: NotificationService,
        settings: RelaySettings,
    ) -> None:
        self._repository = repository
        self._sources: dict[
            tuple[ResidentBackend | None, ResidentEngine], ResidentNotificationSource
        ] = {
            (controller.runtime_backend, controller.engine): controller
            for controller in session_controllers
            if isinstance(controller, ResidentNotificationSource)
        }
        self._notifications = notifications
        self._settings = settings
        self._followers: dict[UUID, asyncio.Task[None]] = {}

    @property
    def following(self) -> frozenset[UUID]:
        """The residents whose outbox is being followed now."""
        return frozenset(self._followers)

    def source_for(self, runtime: ResidentRuntime) -> ResidentNotificationSource | None:
        """The controller that reads ``runtime``'s outbox (the same lookup as its sessions)."""
        return self._sources.get((runtime.backend, runtime.engine)) or self._sources.get(
            (None, runtime.engine)
        )

    async def sync(self) -> None:
        """Follow exactly the residents that can have an outbox now.

        Called with every resident reconciliation pass: an active resident that
        serves sessions (its shim is on) is followed, anything else is let go.
        """
        desired = {
            runtime.id: runtime
            for runtime in await self._repository.list_for_reconciliation()
            if self._followable(runtime)
        }
        for runtime_id in set(self._followers) - set(desired):
            await self._stop_following(runtime_id)
        for runtime_id, runtime in desired.items():
            task = self._followers.get(runtime_id)
            if task is not None and not task.done():
                continue
            if task is not None and not task.cancelled() and task.exception() is not None:
                logger.error(
                    "Following resident %s's notifications failed; following again",
                    runtime.name,
                    exc_info=task.exception(),
                )
            self._followers[runtime_id] = asyncio.create_task(
                self._follow(runtime_id), name=f"resident-notifications-{runtime_id}"
            )
            logger.info("Following resident %s's notifications", runtime.name)

    async def stop(self) -> None:
        for runtime_id in list(self._followers):
            await self._stop_following(runtime_id)

    def _followable(self, runtime: ResidentRuntime) -> bool:
        return (
            runtime.observed_state is ResidentObservedState.ACTIVE
            and runtime.desired_state is ResidentDesiredState.RUNNING
            and ResidentCapability.SESSION_LIST in runtime.capabilities
            and self.source_for(runtime) is not None
        )

    async def _stop_following(self, runtime_id: UUID) -> None:
        task = self._followers.pop(runtime_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _follow(self, runtime_id: UUID) -> None:
        """Drain one resident's outbox, then wait for more, reconnecting on failure."""
        delay = self._settings.reconnect_initial_seconds
        while True:
            runtime = await self._repository.get(runtime_id)
            if runtime is None or not self._followable(runtime):
                return
            source = self.source_for(runtime)
            if source is None:
                return
            try:
                stream = await source.open_notifications(runtime)
            except ResidentNotificationsUnavailableError as exc:
                # Not a fault: an older resident has no outbox. Look again rarely; a
                # redeploy with a current Ravn starts serving one.
                logger.info(
                    "%s; checking again in %.0fs", exc, self._settings.reconnect_max_seconds
                )
                await asyncio.sleep(self._settings.reconnect_max_seconds)
                continue
            except Exception as exc:
                logger.warning(
                    "Cannot read resident %s's notifications (%s: %s); retrying in %.0fs",
                    runtime.name,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._settings.reconnect_max_seconds)
                continue
            try:
                delay = self._settings.reconnect_initial_seconds
                while True:
                    await self._drain(runtime, stream)
                    await stream.wait_for_more(timeout=self._settings.idle_resync_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Lost resident %s's notifications (%s: %s); reconnecting in %.0fs",
                    runtime.name,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._settings.reconnect_max_seconds)
            finally:
                await stream.close()

    async def _drain(self, runtime: ResidentRuntime, stream: ResidentNotificationStream) -> None:
        """Record every pending entry, acknowledging each batch once it is recorded."""
        while True:
            entries = await stream.pending(limit=self._settings.batch_size)
            if not entries:
                return
            await self.apply(runtime, stream, entries)
            await stream.acknowledge(through_seq=entries[-1].seq)
            if len(entries) < self._settings.batch_size:
                return

    async def apply(
        self,
        runtime: ResidentRuntime,
        stream: ResidentNotificationStream,
        entries: list[ResidentNotificationEntry],
    ) -> None:
        """Record one batch in the resident's write order.

        A question already answered within the same batch (a backlog read after
        Forge was away) is not recorded at all: it would push the owner about
        something they have already settled.
        """
        settled = {
            (stream.resident_session_id(entry.channel), request_id)
            for entry in entries
            if entry.op is ResidentNotificationOp.RESOLVED
            for request_id in entry.resolved_request_ids
        }
        for entry in entries:
            resident_session_id = stream.resident_session_id(entry.channel)
            if entry.op is ResidentNotificationOp.ATTENTION:
                if (resident_session_id, entry.request_id) in settled:
                    continue
                await self._notifications.record_resident_attention(
                    runtime,
                    resident_session_id=resident_session_id,
                    kind=AttentionInputKind(entry.input_kind).value,
                    prompt=entry.prompt,
                    request_id=entry.request_id,
                )
            elif entry.op is ResidentNotificationOp.RESOLVED:
                await self._notifications.retire_resident_attention(
                    runtime,
                    resident_session_id=resident_session_id,
                    request_ids=list(entry.resolved_request_ids),
                )
            elif entry.draft is not None:
                try:
                    await self._notifications.submit_for_resident(
                        runtime,
                        entry.draft,
                        resident_session_id=resident_session_id,
                        idempotency_key=entry.idempotency_key,
                    )
                except NotificationValidationError as exc:
                    # Refused for good (like a 4xx): acknowledged with the batch, so one
                    # bad entry cannot hold back everything the resident queued after it.
                    logger.error(
                        "Resident %s's notification %d was refused and dropped: %s",
                        runtime.name,
                        entry.seq,
                        exc,
                    )
