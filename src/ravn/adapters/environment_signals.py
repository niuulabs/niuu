"""Environment signal adapters for resident Valkyries."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from observatory.contracts import ObservatoryFragment
from ravn.domain.environment import (
    Environment,
    inbox_environment_fixture,
    k8s_environment_fixture,
    printer_environment_fixture,
)
from ravn.ports.signal_adapter import NormalizedSignal, NormalizedSignalType, SignalAdapter

RawProvider = Callable[[], Iterable[Any] | list[Any]]


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _field(raw: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        current = raw
        found = True
        for part in name.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                found = False
                break
        if found:
            return current
    return default


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    return datetime.now(UTC)


def _correlation(environment: Environment, dedupe_key: str) -> str:
    return f"{environment.id}:{dedupe_key}"


def signal_sources_observatory_fragment(environment: Environment) -> ObservatoryFragment:
    """Project Environment signal sources into the existing Observatory graph."""
    nodes = []
    edges = []
    for source in environment.signal_sources:
        node_id = f"signal-source:{environment.id}:{source.id}"
        nodes.append(
            {
                "id": node_id,
                "typeId": "service",
                "label": source.name,
                "parentId": environment.topology.node_id,
                "status": "healthy" if source.enabled else "offline",
                "activity": source.kind,
                "zone": environment.topology.zone,
                "cluster": environment.topology.cluster_id or None,
                "hostId": environment.topology.host_id or None,
                "flockId": environment.flock_ids[0] if environment.flock_ids else None,
                "sourceId": source.id,
                "sourceKind": "signal_source",
            }
        )
        edges.append(
            {
                "id": f"{node_id}->{environment.topology.node_id}",
                "sourceId": node_id,
                "targetId": environment.topology.node_id,
                "kind": "dashed-anim" if source.enabled else "soft",
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "sourceId": environment.id,
            "sourceKind": "environment_signal_sources",
            "sourceName": f"{environment.name} signal sources",
            "realmId": environment.realm_id,
            "clusterId": environment.topology.cluster_id,
            "revision": "environment.signals.v1",
        },
    }


class _IterableSignalAdapter(SignalAdapter):
    """Base adapter for fixture, file, webhook, and polled-provider inputs."""

    def __init__(
        self,
        *,
        environment: Environment,
        source_id: str,
        raw_items: Iterable[Any] | None = None,
        provider: RawProvider | None = None,
    ) -> None:
        self.environment = environment
        self._source_id = source_id
        self._raw_items = list(raw_items or [])
        self._provider = provider

    @property
    def source_id(self) -> str:
        return self._source_id

    async def _raw(self) -> list[Any]:
        if self._provider is None:
            return list(self._raw_items)
        provided = self._provider()
        if inspect.isawaitable(provided):
            provided = await provided
        return list(provided)


class KubernetesSignalAdapter(_IterableSignalAdapter):
    """Normalize Kubernetes Event objects for a resident k8s Valkyrie."""

    signal_type: NormalizedSignalType = "kubernetes"

    async def collect(self) -> list[NormalizedSignal]:
        return [self.normalize_event(raw) for raw in await self._raw()]

    @classmethod
    def from_kubernetes_client(
        cls,
        *,
        environment: Environment,
        core_v1: Any,
        source_id: str = "kubernetes-events",
    ) -> KubernetesSignalAdapter:
        """Build from an injected Kubernetes CoreV1Api-like client."""

        def provider() -> Iterable[Any]:
            events = core_v1.list_event_for_all_namespaces()
            return getattr(events, "items", events)

        return cls(environment=environment, source_id=source_id, provider=provider)

    def normalize_event(self, raw: Any) -> NormalizedSignal:
        metadata = _field(raw, "metadata", default={}) or {}
        involved = _field(raw, "involvedObject", "involved_object", default={}) or {}
        namespace = _text(
            _field(involved, "namespace", default=_field(metadata, "namespace", default="default"))
        )
        kind = _text(_field(involved, "kind", default="Object"))
        name = _text(_field(involved, "name", default="unknown"))
        reason = _text(_field(raw, "reason", default="Unknown"))
        event_name = _text(_field(metadata, "name", default=f"{name}.{reason}"))
        uid = _text(_field(metadata, "uid", default=event_name))
        event_type = _text(_field(raw, "type", default="Normal"))
        timestamp = _parse_timestamp(
            _field(raw, "eventTime", "event_time", "lastTimestamp", "last_timestamp")
        )
        severity = "warning" if event_type.lower() == "warning" else "info"
        if reason.lower() in {"failed", "failedscheduling", "oomkilled", "backoff"}:
            severity = "critical"
        dedupe_key = f"k8s:{namespace}:{kind}:{name}:{reason}"
        raw_ref = f"k8s://{namespace}/events/{event_name}#{uid}"
        return NormalizedSignal(
            source_id=self.source_id,
            environment_id=self.environment.id,
            environment_type=self.environment.type,
            signal_type="kubernetes",
            severity=severity,  # type: ignore[arg-type]
            timestamp=timestamp,
            raw_payload_ref=raw_ref,
            normalized_payload={
                "namespace": namespace,
                "kind": kind,
                "name": name,
                "reason": reason,
                "message": _text(_field(raw, "message")),
                "count": _field(raw, "count", default=1),
                "type": event_type,
            },
            dedupe_key=dedupe_key,
            correlation_id=_correlation(self.environment, dedupe_key),
            provider="kubernetes",
            provider_event_id=uid,
            object_ref={
                "api_version": _text(_field(involved, "apiVersion", "api_version")),
                "kind": kind,
                "namespace": namespace,
                "name": name,
                "uid": _text(_field(involved, "uid")),
            },
            provenance={"adapter": "kubernetes.events", "source_id": self.source_id},
        )


class InboxSignalAdapter(_IterableSignalAdapter):
    """Normalize inbox/email messages from a pluggable provider."""

    signal_type: NormalizedSignalType = "email"

    async def collect(self) -> list[NormalizedSignal]:
        return [self.normalize_message(raw) for raw in await self._raw()]

    def normalize_message(self, raw: Any) -> NormalizedSignal:
        message_id = _text(_field(raw, "message_id", "id", default="unknown"))
        thread_id = _text(_field(raw, "thread_id", "threadId", default=message_id))
        sender = _text(_field(raw, "from", "sender", default="unknown"))
        subject = _text(_field(raw, "subject", default=""))
        importance = _text(_field(raw, "importance", "priority", default="normal")).lower()
        severity = "warning" if importance in {"high", "important", "pinned"} else "info"
        timestamp = _parse_timestamp(_field(raw, "received_at", "timestamp", "date"))
        dedupe_key = f"email:{thread_id}:{message_id}"
        provider = _text(_field(raw, "provider", default="inbox"))
        return NormalizedSignal(
            source_id=self.source_id,
            environment_id=self.environment.id,
            environment_type=self.environment.type,
            signal_type="email",
            severity=severity,  # type: ignore[arg-type]
            timestamp=timestamp,
            raw_payload_ref=f"email://{provider}/threads/{thread_id}/messages/{message_id}",
            normalized_payload={
                "message_id": message_id,
                "thread_id": thread_id,
                "from": sender,
                "subject": subject,
                "snippet": _text(_field(raw, "snippet", "body_preview")),
                "labels": list(_field(raw, "labels", default=[]) or []),
                "importance": importance,
            },
            dedupe_key=dedupe_key,
            correlation_id=_correlation(self.environment, dedupe_key),
            provider=provider,
            provider_event_id=message_id,
            object_ref={"thread_id": thread_id, "message_id": message_id, "from": sender},
            provenance={"adapter": "inbox.message", "source_id": self.source_id},
        )


class HostSignalAdapter(_IterableSignalAdapter):
    """Normalize local host events from logs, webhooks, or file providers."""

    signal_type: NormalizedSignalType = "host"

    async def collect(self) -> list[NormalizedSignal]:
        return [self.normalize_host_event(raw) for raw in await self._raw()]

    def normalize_host_event(self, raw: Any) -> NormalizedSignal:
        event_id = _text(_field(raw, "event_id", "id", default="unknown"))
        host_id = _text(_field(raw, "host_id", "host", default=self.environment.topology.host_id))
        severity = _text(_field(raw, "severity", default="info")).lower()
        if severity not in {"debug", "info", "warning", "critical"}:
            severity = "warning"
        category = _text(_field(raw, "category", "kind", default="host"))
        timestamp = _parse_timestamp(_field(raw, "observed_at", "timestamp"))
        dedupe_key = f"host:{host_id}:{category}:{event_id}"
        return NormalizedSignal(
            source_id=self.source_id,
            environment_id=self.environment.id,
            environment_type=self.environment.type,
            signal_type="host",
            severity=severity,  # type: ignore[arg-type]
            timestamp=timestamp,
            raw_payload_ref=f"host://{host_id}/events/{event_id}",
            normalized_payload={
                "event_id": event_id,
                "host_id": host_id,
                "category": category,
                "message": _text(_field(raw, "message")),
            },
            dedupe_key=dedupe_key,
            correlation_id=_correlation(self.environment, dedupe_key),
            provider=_text(_field(raw, "provider", default="host")),
            provider_event_id=event_id,
            object_ref={"host_id": host_id, "event_id": event_id, "category": category},
            provenance={"adapter": "host.event", "source_id": self.source_id},
        )


class PrinterPiSignalAdapter(_IterableSignalAdapter):
    """Normalize printer/Pi telemetry from webhook, MQTT, file, or mock providers."""

    signal_type: NormalizedSignalType = "printer_telemetry"

    async def collect(self) -> list[NormalizedSignal]:
        return [self.normalize_telemetry(raw) for raw in await self._raw()]

    def normalize_telemetry(self, raw: Any) -> NormalizedSignal:
        event_id = _text(_field(raw, "event_id", "id", default="unknown"))
        printer_id = _text(_field(raw, "printer_id", "printer", default="printer"))
        event_kind = _text(_field(raw, "event_type", "type", "kind", default="telemetry"))
        resin_percent = _field(raw, "resin_percent")
        filament_percent = _field(raw, "filament_percent")
        severity = "info"
        if event_kind in {"error", "fault", "sensor_error"}:
            severity = "critical"
        elif event_kind in {"resin_low", "filament_low"}:
            severity = "warning"
        timestamp = _parse_timestamp(_field(raw, "observed_at", "timestamp"))
        dedupe_key = f"printer:{printer_id}:{event_kind}:{event_id}"
        return NormalizedSignal(
            source_id=self.source_id,
            environment_id=self.environment.id,
            environment_type=self.environment.type,
            signal_type="printer_telemetry",
            severity=severity,  # type: ignore[arg-type]
            timestamp=timestamp,
            raw_payload_ref=f"printer://{printer_id}/events/{event_id}",
            normalized_payload={
                "event_id": event_id,
                "printer_id": printer_id,
                "event_type": event_kind,
                "status": _text(_field(raw, "status")),
                "message": _text(_field(raw, "message")),
                "resin_percent": resin_percent,
                "filament_percent": filament_percent,
            },
            dedupe_key=dedupe_key,
            correlation_id=_correlation(self.environment, dedupe_key),
            provider=_text(_field(raw, "provider", default="printer-pi")),
            provider_event_id=event_id,
            object_ref={"printer_id": printer_id, "event_id": event_id, "event_type": event_kind},
            provenance={"adapter": "printer.telemetry", "source_id": self.source_id},
        )


def demo_signal_adapters() -> list[SignalAdapter]:
    """Return deterministic adapters for the Environment MVP demo."""
    k8s = k8s_environment_fixture()
    inbox = inbox_environment_fixture()
    printer = printer_environment_fixture()
    return [
        KubernetesSignalAdapter(
            environment=k8s,
            source_id="kubernetes-events",
            raw_items=[
                {
                    "metadata": {"name": "api-rollout", "uid": "ev-k8s-rollout"},
                    "involvedObject": {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "namespace": "shop",
                        "name": "api",
                        "uid": "deploy-api",
                    },
                    "type": "Normal",
                    "reason": "ScalingReplicaSet",
                    "message": "Scaled up replica set during deploy",
                    "eventTime": "2026-06-03T12:00:00Z",
                },
                {
                    "metadata": {"name": "api-oom", "uid": "ev-k8s-oom"},
                    "involvedObject": {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "namespace": "shop",
                        "name": "api-7d9",
                        "uid": "pod-api-7d9",
                    },
                    "type": "Warning",
                    "reason": "OOMKilled",
                    "message": "Container was terminated by OOM killer",
                    "eventTime": "2026-06-03T12:05:00Z",
                },
            ],
        ),
        InboxSignalAdapter(
            environment=inbox,
            source_id="gmail-inbox",
            raw_items=[
                {
                    "id": "msg-newsletter",
                    "thread_id": "thread-newsletter",
                    "from": "updates@example.com",
                    "subject": "Weekly digest",
                    "importance": "normal",
                    "received_at": "2026-06-03T12:10:00Z",
                    "provider": "gmail",
                },
                {
                    "id": "msg-renewal",
                    "thread_id": "thread-renewal",
                    "from": "customer@example.com",
                    "subject": "Renewal question",
                    "importance": "high",
                    "received_at": "2026-06-03T12:12:00Z",
                    "provider": "gmail",
                },
            ],
        ),
        HostSignalAdapter(
            environment=inbox,
            source_id="host-events",
            raw_items=[
                {
                    "id": "host-power",
                    "host_id": "jozef-mac",
                    "category": "power",
                    "severity": "info",
                    "message": "Host woke from sleep",
                    "observed_at": "2026-06-03T12:14:00Z",
                }
            ],
        ),
        PrinterPiSignalAdapter(
            environment=printer,
            source_id="moonraker-telemetry",
            raw_items=[
                {
                    "id": "print-done",
                    "printer": "saturn-4",
                    "type": "print_done",
                    "status": "complete",
                    "observed_at": "2026-06-03T12:20:00Z",
                },
                {
                    "id": "resin-low",
                    "printer": "saturn-4",
                    "type": "resin_low",
                    "resin_percent": 8,
                    "observed_at": "2026-06-03T12:22:00Z",
                },
                {
                    "id": "z-axis-fault",
                    "printer": "saturn-4",
                    "type": "error",
                    "message": "Z axis failed homing check",
                    "observed_at": "2026-06-03T12:25:00Z",
                },
            ],
        ),
    ]
