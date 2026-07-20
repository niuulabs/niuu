"""Port contracts for Environment signal adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator

from ravn.domain.environment import Environment, EnvironmentType, apply_environment_metadata
from sleipnir.domain.catalog import signal_received
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

SignalSeverity = Literal["debug", "info", "warning", "critical"]
NormalizedSignalType = Literal[
    "generic",
    "kubernetes",
    "webhook",
    "metrics",
    "logs",
]

_CATALOG_SIGNAL_KIND: dict[str, str] = {
    "generic": "generic",
    "kubernetes": "kubernetes",
    "webhook": "received",
    "metrics": "received",
    "logs": "received",
}


class NormalizedSignal(BaseModel):
    """Provider-neutral signal consumed by resident Valkyries."""

    source_id: str
    environment_id: str
    environment_type: EnvironmentType
    signal_type: NormalizedSignalType
    severity: SignalSeverity
    timestamp: datetime
    raw_payload_ref: str
    normalized_payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str
    correlation_id: str
    causation_id: str | None = None
    provider: str = ""
    provider_event_id: str = ""
    object_ref: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dedupe_key", "correlation_id")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    def to_event(
        self,
        *,
        source: str,
        environment: Environment | None = None,
        tenant_id: str | None = None,
    ) -> SleipnirEvent:
        """Convert to the standard Sleipnir ``signal.*`` catalog event."""
        data = {
            "source_id": self.source_id,
            "raw_payload_ref": self.raw_payload_ref,
            "dedupe_key": self.dedupe_key,
            "provider": self.provider,
            "provider_event_id": self.provider_event_id,
            "object_ref": self.object_ref,
            "provenance": self.provenance,
            "observed_at": self.timestamp.isoformat(),
            **self.normalized_payload,
        }
        event = signal_received(
            environment_id=self.environment_id,
            environment_type=self.environment_type,
            signal_source=self.source_id,
            signal_kind=_CATALOG_SIGNAL_KIND[self.signal_type],
            severity=self.severity,
            data=data,
            source=source,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            tenant_id=tenant_id,
        )
        event.timestamp = self.timestamp
        event.event_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    f"ravn:signal:{self.environment_id}:{self.source_id}:"
                    f"{self.provider_event_id or self.dedupe_key}"
                ),
            )
        )
        if environment is not None:
            apply_environment_metadata(event, environment)
        return event


class SignalAdapter(ABC):
    """Collect and publish normalized Environment signals."""

    environment: Environment

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable source identifier within the Environment."""

    @property
    @abstractmethod
    def signal_type(self) -> NormalizedSignalType:
        """Provider-neutral signal type emitted by this adapter."""

    @abstractmethod
    async def collect(self) -> list[NormalizedSignal]:
        """Collect provider signals and normalize them."""

    @property
    def requires_commit(self) -> bool:
        """Whether collected signals remain pending until explicitly committed."""
        return False

    async def commit(self) -> None:
        """Acknowledge the most recently collected batch after durable handling."""

    async def rollback(self) -> None:
        """Release the most recently collected batch for retry after failure."""

    async def publish(self, publisher: SleipnirPublisher) -> list[SleipnirEvent]:
        """Collect, convert, and publish signals over Sleipnir."""
        try:
            events = [
                signal.to_event(
                    source=f"adapter:{self.source_id}",
                    environment=self.environment,
                    tenant_id=self.environment.tenant_id,
                )
                for signal in await self.collect()
            ]
            await publisher.publish_batch(events)
            await self.commit()
            return events
        except BaseException as exc:
            try:
                await self.rollback()
            except Exception as rollback_exc:
                exc.add_note(f"signal rollback also failed: {rollback_exc}")
            raise
