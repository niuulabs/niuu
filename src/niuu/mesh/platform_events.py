"""Signed platform events: the proof that a "plan was approved" came from a platform service.

Forge, Ting and the other platform services publish events on the Sleipnir bus
("a plan was approved", "a gate opened"), and residents and Ting's triggers act on them. Anyone
allowed on the bus could otherwise publish one. So a platform service signs what it publishes
with the same machinery as the mesh (:mod:`niuu.mesh.attestation`), under a ``service`` (or
Forge's ``authority``) certificate, and whoever *acts* on a platform event checks it.

* :class:`SigningBus` wraps the service's bus and signs every event except the mesh's own
  (``mesh``), which peers sign with their own certificates.
* :class:`PlatformEventVerifier` is called by a subscriber at the point where it would act: an
  event that is unsigned, signed by anyone but a platform service, altered, or stale is refused
  with the reason, logged at error and counted (``niuu.mesh.attestation.refusals``,
  direction ``platform_event``). A mesh peer's certificate (a session, a resident) is refused
  as ``not_platform_service``.

The signature is carried in the event's payload under :data:`ATTESTATION_KEY` and removed again
when an event is accepted, so consumers see the payload that was published.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from niuu.adapters.mesh.attested import record_refusal
from niuu.mesh import mesh_event_prefix
from niuu.mesh.attestation import (
    PURPOSE_PLATFORM_EVENT,
    AttestationReason,
    AttestationRefusedError,
    MeshAttestationVerifier,
    MeshSigner,
)
from sleipnir.domain.events import SleipnirEvent

#: Payload key carrying the attestation of a platform event.
ATTESTATION_KEY = "_attestation"

DIRECTION_PLATFORM_EVENT = "platform_event"


def platform_subject(event: SleipnirEvent) -> dict[str, Any]:
    """What a platform event signature covers: everything a subscriber acts on."""
    payload = {k: v for k, v in event.payload.items() if k != ATTESTATION_KEY}
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "source": event.source,
        "payload": payload,
        "summary": event.summary,
        "urgency": event.urgency,
        "domain": event.domain,
        "correlation_id": event.correlation_id,
        "tenant_id": event.tenant_id,
    }


def _is_mesh_event(event: SleipnirEvent, prefixes: tuple[str, ...]) -> bool:
    return event.event_type.startswith(prefixes)


class SigningBus:
    """A Sleipnir bus whose publishes are signed as a platform service.

    Everything else (``start``, ``stop``, ``subscribe``, ...) is the wrapped bus's own.
    """

    def __init__(
        self, bus: Any, signer: MeshSigner, *, mesh_prefixes: tuple[str, ...] = ()
    ) -> None:
        self._bus = bus
        self._signer = signer
        self._mesh_prefixes = mesh_prefixes or (mesh_event_prefix(""),)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bus, name)

    @property
    def signer(self) -> MeshSigner:
        return self._signer

    def _signed(self, event: SleipnirEvent) -> SleipnirEvent:
        if _is_mesh_event(event, self._mesh_prefixes):
            return event
        attestation = self._signer.attest(PURPOSE_PLATFORM_EVENT, platform_subject(event))
        return replace(event, payload={**event.payload, ATTESTATION_KEY: attestation})

    async def publish(self, event: SleipnirEvent) -> None:
        await self._bus.publish(self._signed(event))

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        await self._bus.publish_batch([self._signed(event) for event in events])


class PlatformEventVerifier:
    """Checks that a platform event was published by a platform service."""

    def __init__(
        self,
        verifier: MeshAttestationVerifier,
        *,
        event_max_age_s: float,
        on_refusal: Callable[[AttestationRefusedError, str, str], None] | None = None,
    ) -> None:
        self._verifier = verifier
        self._max_age_ms = int(event_max_age_s * 1000)
        self._on_refusal = on_refusal

    def verify(self, event: SleipnirEvent) -> SleipnirEvent:
        """The event as published, or raise :class:`AttestationRefusedError`."""
        attestation = event.payload.get(ATTESTATION_KEY)
        self._verifier.verify(
            attestation,
            PURPOSE_PLATFORM_EVENT,
            platform_subject(event),
            claimed=event.source,
            platform=True,
            max_age_ms=self._max_age_ms,
            check_membership=False,
        )
        payload = {k: v for k, v in event.payload.items() if k != ATTESTATION_KEY}
        return replace(event, payload=payload)

    def accept(self, event: SleipnirEvent) -> SleipnirEvent | None:
        """The verified event, or ``None`` after logging and counting the refusal."""
        try:
            return self.verify(event)
        except AttestationRefusedError as error:
            record_refusal(error, DIRECTION_PLATFORM_EVENT, event.event_type)
            if self._on_refusal is not None:
                self._on_refusal(error, DIRECTION_PLATFORM_EVENT, event.event_type)
            return None


__all__ = [
    "ATTESTATION_KEY",
    "DIRECTION_PLATFORM_EVENT",
    "AttestationReason",
    "PlatformEventVerifier",
    "SigningBus",
    "platform_subject",
]
