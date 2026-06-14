"""Canonical discovery adapters for Observatory topology."""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from niuu.utils import import_class, resolve_secret_kwargs
from observatory.contracts import ObservatoryEdge, ObservatoryEvent, ObservatorySnapshot

logger = logging.getLogger(__name__)
_SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_KNOWN_TYPE_IDS = {
    "bifrost",
    "cluster",
    "mimir",
    "namespace",
    "ravn_long",
    "service",
    "skuld",
    "ting",
    "volundr",
}
_COMPONENT_TYPES = {
    "agent": "ravn_long",
    "api": "volundr",
    "bifrost": "bifrost",
    "gateway": "bifrost",
    "guild": "service",
    "knowledge-service": "mimir",
    "mimir": "mimir",
    "observatory": "service",
    "ravn": "ravn_long",
    "ravn-api": "ravn_long",
    "saga-coordinator": "ting",
    "shared-services": "service",
    "ting": "ting",
    "volundr": "volundr",
    "web": "volundr",
    "web-next": "volundr",
}


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(ts: datetime | None = None) -> str:
    return (ts or _utc_now()).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "entity"


def _clean_map(value: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _status_from_k8s(kind: str, payload: dict[str, Any]) -> str:
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    if kind == "deployment":
        desired = int(status.get("replicas") or 0)
        ready = int(status.get("readyReplicas") or 0)
        available = int(status.get("availableReplicas") or 0)
        if desired == 0:
            return "unknown"
        return "healthy" if ready >= desired and available >= desired else "failed"
    if kind == "pod":
        phase = str(status.get("phase") or "").lower()
        if phase == "running":
            return "healthy"
        if phase in {"failed", "unknown"}:
            return "failed"
        return "unknown"
    return "healthy"


@dataclass(frozen=True)
class DiscoveryResult:
    """Output from one discovery adapter."""

    entities: list[DiscoveredEntity] = field(default_factory=list)
    edges: list[ObservatoryEdge] = field(default_factory=list)
    events: list[ObservatoryEvent] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveredEntity:
    """Normalized runtime entity discovered from one source."""

    id: str
    kind: str
    name: str
    cluster: str = ""
    namespace: str = ""
    status: str = "unknown"
    parent_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    source_adapter: str = ""
    source_kind: str = ""
    source_uid: str = ""
    endpoints: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class DiscoveryAdapter(Protocol):
    """Adapter contract for Observatory discovery sources."""

    async def discover(self) -> DiscoveryResult:
        """Return discovered entities, edges, and events."""


class LocalHostDiscoveryAdapter:
    """Expose the local host as a discovery source."""

    def __init__(self, name: str = "", cluster: str = "", enabled: bool = True) -> None:
        self._name = name or socket.gethostname()
        self._cluster = cluster
        self._enabled = enabled

    async def discover(self) -> DiscoveryResult:
        if not self._enabled:
            return DiscoveryResult()
        host_id = f"local:{_slug(self._cluster or self._name)}:{_slug(self._name)}"
        return DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id=host_id,
                    kind="service",
                    name=self._name,
                    cluster=self._cluster,
                    status="healthy",
                    source_adapter=self.__class__.__name__,
                    source_kind="localhost",
                    metadata={"host": self._name},
                )
            ]
        )


class WardenSpecDiscoveryAdapter:
    """Discover locally persisted WardenSpec records."""

    def __init__(self, root: str = "", cluster: str = "", namespace: str = "") -> None:
        self._root = root
        self._cluster = cluster
        self._namespace = namespace

    async def discover(self) -> DiscoveryResult:
        try:
            from ravn.adapters.warden_discovery.spec import (  # noqa: PLC0415
                WardenSpecDiscoveryAdapter as RavnWardenSpecDiscoveryAdapter,
            )
        except ImportError:
            return DiscoveryResult(
                events=[
                    _adapter_warning(
                        "wardenspec",
                        "Ravn WardenSpec adapter unavailable",
                    )
                ]
            )

        try:
            wardens = await RavnWardenSpecDiscoveryAdapter(root=self._root).list_wardens()
        except Exception as exc:
            return DiscoveryResult(events=[_adapter_warning("wardenspec", str(exc))])

        entities: list[DiscoveredEntity] = []
        for warden in wardens:
            warden_id = str(getattr(warden, "id", "") or "").strip()
            if not warden_id:
                continue
            deployment_kwargs = getattr(warden, "deployment_kwargs", {}) or {}
            cluster = str(deployment_kwargs.get("cluster") or self._cluster)
            namespace = str(deployment_kwargs.get("namespace") or self._namespace)
            entities.append(
                DiscoveredEntity(
                    id=(
                        f"warden:{_slug(cluster or 'local')}:"
                        f"{_slug(namespace or 'default')}:{_slug(warden_id)}"
                    ),
                    kind="ravn_long",
                    name=str(getattr(warden, "name", "") or warden_id),
                    cluster=cluster,
                    namespace=namespace,
                    status=(
                        "healthy"
                        if str(
                            getattr(getattr(warden, "runtime", None), "state", "")
                        ).lower()
                        == "active"
                        else "unknown"
                    ),
                    source_adapter=self.__class__.__name__,
                    source_kind="wardenspec",
                    source_uid=warden_id,
                    metadata={"persona": str(getattr(warden, "persona", "") or "")},
                )
            )
        return DiscoveryResult(entities=entities)


class HttpObservatoryDiscoveryAdapter:
    """Merge topology from another Observatory HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        headers: dict[str, str] | None = None,
        auth_header_env: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._headers = dict(headers or {})
        self._auth_header_env = auth_header_env
        self._transport = transport

    async def discover(self) -> DiscoveryResult:
        headers = dict(self._headers)
        if self._auth_header_env:
            token = os.environ.get(self._auth_header_env, "").strip()
            if token:
                headers.setdefault("Authorization", token)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(self._snapshot_url(), headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return DiscoveryResult(
                events=[_adapter_warning("http-observatory", f"{self._base_url}: {exc}")]
            )
        if not isinstance(payload, dict):
            return DiscoveryResult()
        return DiscoveryResult(
            entities=[
                _entity_from_node(node, source_adapter=self.__class__.__name__)
                for node in payload.get("nodes", [])
                if isinstance(node, dict)
            ],
            edges=[edge for edge in payload.get("edges", []) if _is_edge(edge)],
            events=[event for event in payload.get("events", []) if isinstance(event, dict)],
        )

    def _snapshot_url(self) -> str:
        if self._base_url.endswith("/api/v1/observatory"):
            return f"{self._base_url}/topology/snapshot"
        return f"{self._base_url}/api/v1/observatory/topology/snapshot"


class KubernetesDiscoveryAdapter:
    """Discover labeled Kubernetes resources through the in-cluster REST API."""

    def __init__(
        self,
        cluster: str = "",
        namespace: str = "",
        label_selector: str = "niuu.world/cluster",
        include_kinds: list[str] | None = None,
        timeout_seconds: float = 10.0,
        service_account_root: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cluster = cluster
        self._namespace = namespace
        self._label_selector = label_selector
        self._include_kinds = include_kinds or ["deployments", "services", "pods", "ingresses"]
        self._timeout_seconds = timeout_seconds
        self._service_account_root = (
            Path(service_account_root) if service_account_root else _SERVICE_ACCOUNT_ROOT
        )
        self._transport = transport

    async def discover(self) -> DiscoveryResult:
        token_path = self._service_account_root / "token"
        ca_path = self._service_account_root / "ca.crt"
        if not token_path.exists():
            return DiscoveryResult(
                events=[
                    _adapter_warning(
                        "kubernetes",
                        "Kubernetes service account token not mounted",
                    )
                ]
            )

        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        base_url = f"https://{host}:{port}"
        headers = {"Authorization": f"Bearer {token_path.read_text(encoding='utf-8').strip()}"}
        entities: list[DiscoveredEntity] = []
        events: list[ObservatoryEvent] = []
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                verify=str(ca_path) if ca_path.exists() else True,
                transport=self._transport,
            ) as client:
                for kind in self._include_kinds:
                    path = self._path_for_kind(kind)
                    if not path:
                        continue
                    response = await client.get(
                        f"{base_url}{path}",
                        headers=headers,
                        params=(
                            {"labelSelector": self._label_selector}
                            if self._label_selector
                            else None
                        ),
                    )
                    if response.status_code == 403:
                        events.append(_adapter_warning("kubernetes", f"Forbidden listing {kind}"))
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    for item in payload.get("items", []) if isinstance(payload, dict) else []:
                        if isinstance(item, dict):
                            entity = self._entity_from_k8s(kind.rstrip("s"), item)
                            if entity is not None:
                                entities.append(entity)
        except Exception as exc:
            events.append(_adapter_warning("kubernetes", str(exc)))
        return DiscoveryResult(entities=entities, events=events)

    def _path_for_kind(self, kind: str) -> str:
        namespace = quote(self._namespace, safe="")
        if kind == "deployments":
            if namespace:
                return f"/apis/apps/v1/namespaces/{namespace}/deployments"
            return "/apis/apps/v1/deployments"
        if kind in {"services", "pods"}:
            if namespace:
                return f"/api/v1/namespaces/{namespace}/{kind}"
            return f"/api/v1/{kind}"
        if kind == "ingresses":
            if namespace:
                return f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses"
            return "/apis/networking.k8s.io/v1/ingresses"
        return ""

    def _entity_from_k8s(self, resource_kind: str, item: dict[str, Any]) -> DiscoveredEntity | None:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        labels = _clean_map(metadata.get("labels"))
        annotations = _clean_map(metadata.get("annotations"))
        name = str(metadata.get("name") or "").strip()
        namespace = str(
            metadata.get("namespace") or labels.get("niuu.world/namespace") or self._namespace
        )
        if not name:
            return None
        cluster = labels.get("niuu.world/cluster") or self._cluster or "unknown"
        component = labels.get("niuu.world/kind") or labels.get("app.kubernetes.io/component")
        app_name = labels.get("app.kubernetes.io/name")
        type_id = _COMPONENT_TYPES.get(
            component or "",
            _COMPONENT_TYPES.get(app_name or "", "service"),
        )
        if resource_kind == "pod":
            type_id = "service"
        entity_id = f"k8s:{_slug(cluster)}:{_slug(namespace)}:{resource_kind}:{_slug(name)}"
        return DiscoveredEntity(
            id=entity_id,
            kind=type_id if type_id in _KNOWN_TYPE_IDS else "service",
            name=name,
            cluster=cluster,
            namespace=namespace,
            status=_status_from_k8s(resource_kind, item),
            labels=labels,
            annotations=annotations,
            source_adapter=self.__class__.__name__,
            source_kind=f"kubernetes:{resource_kind}",
            source_uid=str(metadata.get("uid") or ""),
            endpoints=_endpoints_for_k8s(resource_kind, item, labels),
            metadata={
                "resourceKind": resource_kind,
                "component": component or "",
                "app": app_name or "",
                "generation": metadata.get("generation"),
            },
        )


class CompositeDiscoveryAdapter:
    """Run multiple discovery adapters and merge their outputs."""

    def __init__(self, adapters: list[DiscoveryAdapter]) -> None:
        self._adapters = adapters

    async def discover(self) -> DiscoveryResult:
        if not self._adapters:
            return DiscoveryResult(
                events=[
                    _adapter_warning(
                        "composite",
                        "No Observatory discovery adapters are configured",
                    )
                ]
            )
        entities_by_id: dict[str, DiscoveredEntity] = {}
        edges_by_id: dict[str, ObservatoryEdge] = {}
        events: list[ObservatoryEvent] = []
        for adapter in self._adapters:
            try:
                result = await adapter.discover()
            except Exception as exc:
                logger.warning("Observatory discovery adapter failed: %s", exc)
                result = DiscoveryResult(
                    events=[_adapter_warning(adapter.__class__.__name__, str(exc))]
                )
            for entity in result.entities:
                entities_by_id[entity.id] = entity
            for edge in result.edges:
                edges_by_id[edge["id"]] = edge
            events.extend(result.events)
        return DiscoveryResult(
            entities=list(entities_by_id.values()),
            edges=list(edges_by_id.values()),
            events=events,
        )


def build_discovery_adapter(configs: list[Any]) -> DiscoveryAdapter:
    """Build a composite discovery adapter from dynamic adapter configs."""
    adapters: list[DiscoveryAdapter] = []
    for config in configs:
        adapter_path = str(getattr(config, "adapter", "") or "").strip()
        if not adapter_path:
            continue
        kwargs = resolve_secret_kwargs(
            getattr(config, "kwargs", {}) or {},
            getattr(config, "secret_kwargs_env", {}) or {},
        )
        cls = import_class(adapter_path)
        adapters.append(cls(**kwargs))
    return CompositeDiscoveryAdapter(adapters)


def topology_from_discovery(result: DiscoveryResult) -> ObservatorySnapshot:
    """Materialize an Observatory topology from canonical discovered entities."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, ObservatoryEdge] = {}

    for entity in sorted(
        result.entities,
        key=lambda item: (item.cluster, item.namespace, item.kind, item.name),
    ):
        cluster_id = _cluster_id(entity.cluster)
        if entity.cluster:
            nodes.setdefault(
                cluster_id,
                {
                    "id": cluster_id,
                    "typeId": "cluster",
                    "label": entity.cluster,
                    "parentId": None,
                    "status": "healthy",
                    "sourceKind": "discovery",
                    "clusterName": entity.cluster,
                    "layoutHints": {"mode": "pack", "scope": "world", "packGroup": "cluster"},
                },
            )
        namespace_id = _namespace_id(entity.cluster, entity.namespace)
        parent_id = entity.parent_id
        if entity.cluster and entity.namespace:
            nodes.setdefault(
                namespace_id,
                {
                    "id": namespace_id,
                    "typeId": "namespace",
                    "label": entity.namespace,
                    "parentId": cluster_id,
                    "status": "healthy",
                    "sourceKind": "kubernetes:namespace",
                    "clusterName": entity.cluster,
                    "namespace": entity.namespace,
                    "layoutHints": {"mode": "pack", "scope": "cluster", "packGroup": "namespace"},
                },
            )
            parent_id = parent_id or namespace_id
        elif entity.cluster:
            parent_id = parent_id or cluster_id

        node = _entity_to_node(entity, parent_id=parent_id)
        nodes[node["id"]] = node
        if parent_id:
            edge_id = f"edge:{parent_id}:{node['id']}"
            edges.setdefault(
                edge_id,
                {"id": edge_id, "sourceId": parent_id, "targetId": node["id"], "kind": "soft"},
            )

    for edge in result.edges:
        edges[edge["id"]] = edge

    return {
        "timestamp": _iso(),
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "events": result.events,
        "layoutHints": {"mode": "pack", "scope": "world"},
    }


def _entity_to_node(entity: DiscoveredEntity, *, parent_id: str | None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": entity.id,
        "typeId": entity.kind if entity.kind in _KNOWN_TYPE_IDS else "service",
        "label": entity.name,
        "parentId": parent_id,
        "status": entity.status,
        "sourceKind": entity.source_kind,
        "sourceId": entity.source_uid or entity.id,
        "clusterName": entity.cluster,
        "namespace": entity.namespace,
        "labels": entity.labels,
        "endpoints": entity.endpoints,
        "layoutHints": {"mode": "pack", "scope": "node", "packGroup": entity.kind},
    }
    node.update(entity.metadata)
    return node


def _entity_from_node(node: dict[str, Any], *, source_adapter: str) -> DiscoveredEntity:
    return DiscoveredEntity(
        id=str(node.get("id") or ""),
        kind=str(node.get("typeId") or "service"),
        name=str(node.get("label") or node.get("id") or ""),
        cluster=str(node.get("clusterName") or ""),
        namespace=str(node.get("namespace") or ""),
        status=str(node.get("status") or "unknown"),
        parent_id=node.get("parentId") if isinstance(node.get("parentId"), str) else None,
        labels=_clean_map(node.get("labels")),
        source_adapter=source_adapter,
        source_kind=str(node.get("sourceKind") or "remote-observatory"),
        source_uid=str(node.get("sourceId") or ""),
        endpoints=node.get("endpoints") if isinstance(node.get("endpoints"), dict) else {},
        metadata={key: value for key, value in node.items() if key not in _NODE_ENTITY_KEYS},
    )


_NODE_ENTITY_KEYS = {
    "id",
    "typeId",
    "label",
    "parentId",
    "status",
    "clusterName",
    "namespace",
    "labels",
    "sourceKind",
    "sourceId",
    "endpoints",
    "layoutHints",
}


def _adapter_warning(adapter: str, message: str) -> ObservatoryEvent:
    event_id = f"discovery:{_slug(adapter)}:{_slug(message)[:40]}"
    return {
        "id": event_id,
        "type": "warning",
        "level": "warning",
        "service": "observatory",
        "subject": adapter,
        "body": message,
        "message": message,
        "timestamp": _iso(),
    }


def _cluster_id(cluster: str) -> str:
    return f"cluster-{_slug(cluster or 'unknown')}"


def _namespace_id(cluster: str, namespace: str) -> str:
    return f"namespace-{_slug(cluster or 'unknown')}-{_slug(namespace or 'unknown')}"


def _is_edge(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("sourceId"), str)
        and isinstance(value.get("targetId"), str)
        and isinstance(value.get("kind"), str)
    )


def _endpoints_for_k8s(
    resource_kind: str,
    item: dict[str, Any],
    labels: dict[str, str],
) -> dict[str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    namespace = str(metadata.get("namespace") or labels.get("niuu.world/namespace") or "")
    name = str(metadata.get("name") or "")
    endpoints: dict[str, str] = {}
    public = labels.get("niuu.world/public-url")
    if public:
        endpoints["public"] = public
    if resource_kind == "service" and namespace and name:
        endpoints["internal"] = f"http://{name}.{namespace}.svc.cluster.local"
    if resource_kind == "ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        hosts = [
            str(rule.get("host"))
            for rule in rules
            if isinstance(rule, dict) and rule.get("host")
        ]
        if hosts:
            endpoints["public"] = f"https://{hosts[0]}"
    return endpoints
