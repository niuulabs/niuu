"""Environment signal adapters for resident Valkyries."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from observatory.contracts import ObservatoryFragment
from ravn.domain.environment import (
    Environment,
    k8s_environment_fixture,
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
                "relationType": "signals_to",
                "label": "signals",
                "confidence": "declared",
                "evidence": {
                    "adapter": "environment_signals",
                    "field": "Environment.signal_sources",
                },
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
        raw_items_file: str = "",
        provider: RawProvider | None = None,
        **_: Any,
    ) -> None:
        self.environment = environment
        self._source_id = source_id
        self._raw_items = list(raw_items or [])
        self._raw_items_file = raw_items_file
        self._provider = provider

    @property
    def source_id(self) -> str:
        return self._source_id

    async def _raw(self) -> list[Any]:
        if self._raw_items_file:
            return self._raw_from_file()
        if self._provider is None:
            return list(self._raw_items)
        provided = self._provider()
        if inspect.isawaitable(provided):
            provided = await provided
        return list(provided)

    def _raw_from_file(self) -> list[Any]:
        """Read raw items from a JSON file that may appear after startup.

        A missing file means no signals yet — deterministic injection for
        demos and end-to-end proofs.  A present-but-invalid file is an error.
        """
        path = Path(self._raw_items_file).expanduser()
        if not path.is_file():
            return []
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError(f"raw_items_file must contain a JSON array: {path}")
        return items


class KubernetesSignalAdapter(_IterableSignalAdapter):
    """Normalize Kubernetes Event objects for a resident k8s Valkyrie."""

    signal_type: NormalizedSignalType = "kubernetes"

    def __init__(
        self,
        *,
        environment: Environment,
        source_id: str = "kubernetes-events",
        raw_items: Iterable[Any] | None = None,
        raw_items_file: str = "",
        provider: RawProvider | None = None,
        core_v1: Any | None = None,
        in_cluster: bool = False,
        kubeconfig_env: str = "",
        kubeconfig_path: str = "",
        namespaces: list[str] | None = None,
        include_reasons: list[str] | None = None,
        exclude_reasons: list[str] | None = None,
        field_selector: str = "",
        label_selector: str = "",
        limit: int | None = None,
        max_event_age_seconds: float | None = None,
        critical_reasons: list[str] | None = None,
    ) -> None:
        self._core_v1 = core_v1
        self._in_cluster = in_cluster
        self._kubeconfig_env = kubeconfig_env
        self._kubeconfig_path = kubeconfig_path
        self._namespaces = list(namespaces or [])
        self._include_reasons = {reason.lower() for reason in include_reasons or []}
        # Which reasons escalate to critical is operator judgment, not a
        # baked-in opinion: default is none — severity then only mirrors
        # Kubernetes's own Normal/Warning event classification.
        self._critical_reasons = {reason.lower() for reason in critical_reasons or []}
        self._exclude_reasons = {reason.lower() for reason in exclude_reasons or []}
        self._field_selector = field_selector
        self._label_selector = label_selector
        self._limit = limit
        self._max_event_age = (
            timedelta(seconds=max_event_age_seconds)
            if max_event_age_seconds is not None and max_event_age_seconds > 0
            else None
        )
        self._client_loaded = core_v1 is not None
        selected_provider = provider
        if selected_provider is None and raw_items is None and not raw_items_file:
            selected_provider = self._provider_from_kubernetes
        super().__init__(
            environment=environment,
            source_id=source_id,
            raw_items=raw_items,
            raw_items_file=raw_items_file,
            provider=selected_provider,
        )

    async def collect(self) -> list[NormalizedSignal]:
        raw_items = await self._raw()
        if self._max_event_age is not None:
            cutoff = datetime.now(UTC) - self._max_event_age
            raw_items = [
                raw
                for raw in raw_items
                if _parse_timestamp(
                    _field(raw, "eventTime", "event_time", "lastTimestamp", "last_timestamp")
                )
                >= cutoff
            ]
        signals = [self.normalize_event(raw) for raw in raw_items]
        if self._include_reasons:
            signals = [
                signal
                for signal in signals
                if _text(signal.normalized_payload.get("reason")).lower() in self._include_reasons
            ]
        if self._exclude_reasons:
            signals = [
                signal
                for signal in signals
                if _text(signal.normalized_payload.get("reason")).lower()
                not in self._exclude_reasons
            ]
        return signals

    @classmethod
    def from_kubernetes_client(
        cls,
        *,
        environment: Environment,
        core_v1: Any,
        source_id: str = "kubernetes-events",
        critical_reasons: list[str] | None = None,
    ) -> KubernetesSignalAdapter:
        """Build from an injected Kubernetes CoreV1Api-like client."""

        return cls(
            environment=environment,
            source_id=source_id,
            core_v1=core_v1,
            critical_reasons=critical_reasons,
        )

    async def _provider_from_kubernetes(self) -> list[Any]:
        core_v1 = await self._load_core_v1()
        list_kwargs: dict[str, Any] = {}
        if self._field_selector:
            list_kwargs["field_selector"] = self._field_selector
        if self._label_selector:
            list_kwargs["label_selector"] = self._label_selector
        if self._limit is not None:
            list_kwargs["limit"] = self._limit

        items: list[Any] = []
        if self._namespaces:
            for namespace in self._namespaces:
                result = core_v1.list_namespaced_event(namespace, **list_kwargs)
                if inspect.isawaitable(result):
                    result = await result
                items.extend(list(getattr(result, "items", result)))
            return items

        result = core_v1.list_event_for_all_namespaces(**list_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return list(getattr(result, "items", result))

    async def _load_core_v1(self) -> Any:
        if self._core_v1 is not None and self._client_loaded:
            return self._core_v1
        try:
            from kubernetes_asyncio import client, config
        except ImportError as exc:  # pragma: no cover - exercised in minimal installs
            raise RuntimeError(
                "KubernetesSignalAdapter requires kubernetes-asyncio. "
                "Install niuu with the k8s extra."
            ) from exc

        if self._in_cluster:
            config.load_incluster_config()
        else:
            kubeconfig = self._kubeconfig_path
            if not kubeconfig and self._kubeconfig_env:
                kubeconfig = os.environ.get(self._kubeconfig_env, "")
            await config.load_kube_config(config_file=kubeconfig or None)
        self._core_v1 = client.CoreV1Api()
        self._client_loaded = True
        return self._core_v1

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
        if reason.lower() in self._critical_reasons:
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


class GenericSignalAdapter(_IterableSignalAdapter):
    """Domain-neutral signal ingestion: raw payloads pass through untouched.

    Deliberately encodes NO domain opinions — no field extraction beyond
    identity/time, no severity heuristics. Severity is honored only when the
    SOURCE declares it about itself; everything else is `info`. Residents
    form their own understanding of what the payloads mean; the adapter does
    not contain source-domain classifiers.
    """

    signal_type: NormalizedSignalType = "generic"

    async def collect(self) -> list[NormalizedSignal]:
        return [self.normalize_raw(raw) for raw in await self._raw()]

    def normalize_raw(self, raw: Any) -> NormalizedSignal:
        payload = raw if isinstance(raw, dict) else {"value": raw}
        event_id = _text(_field(payload, "id", "event_id", default=""))
        if not event_id:
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            event_id = digest[:16]
        declared = _text(_field(payload, "severity", default="")).lower()
        severity = declared if declared in {"debug", "info", "warning", "critical"} else "info"
        timestamp = _parse_timestamp(_field(payload, "observed_at", "timestamp", "time"))
        dedupe_key = f"{self.source_id}:{event_id}"
        return NormalizedSignal(
            source_id=self.source_id,
            environment_id=self.environment.id,
            environment_type=self.environment.type,
            signal_type="generic",
            severity=severity,  # type: ignore[arg-type]
            timestamp=timestamp,
            raw_payload_ref=f"generic://{self.source_id}/{event_id}",
            normalized_payload=dict(payload),
            dedupe_key=dedupe_key,
            correlation_id=_correlation(self.environment, dedupe_key),
            provider=_text(_field(payload, "provider", default="generic")),
        )


def demo_signal_adapters() -> list[SignalAdapter]:
    """Return deterministic adapters for the Environment MVP demo."""
    k8s = k8s_environment_fixture()
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
    ]
