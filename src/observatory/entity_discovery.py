"""Canonical discovery adapters for Observatory topology."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from niuu.ports.http_auth import HttpAuthPort
from niuu.utils import import_class, resolve_secret_kwargs
from observatory.contracts import ObservatoryEdge, ObservatoryEvent, ObservatorySnapshot
from observatory.data import REGISTRY

logger = logging.getLogger(__name__)
_SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
#: Entity types recognised when no live registry is supplied.
#:
#: Derived from the registry seed rather than written out by hand. A literal set
#: here inevitably drifts from the registry — that drift is exactly why realms,
#: models and runs were being silently downgraded to ``service``. The registry is
#: the configurable, API-editable source of truth; this is only its in-code
#: default for callers that have no repository to read from.
_SEED_TYPE_IDS = frozenset(str(entry.get("id", "")) for entry in REGISTRY.get("types", []))


def default_type_ids() -> frozenset[str]:
    """Entity type ids known from the registry seed."""
    return _SEED_TYPE_IDS


_COMPONENT_TYPES = {
    "agent": "ravn_long",
    "api": "volundr",
    "bifrost": "bifrost",
    "gateway": "bifrost",
    "guild": "service",
    "knowledge-service": "mimir",
    "mimir": "mimir",
    "observatory": "service",
    "ravn": "service",
    "ravn-api": "service",
    "saga-coordinator": "ting",
    "shared-services": "service",
    "ting": "ting",
    "resident-agent": "valkyrie",
    "valkyrie": "valkyrie",
    "volundr": "volundr",
    "warden": "warden",
    "web": "volundr",
    "web-next": "volundr",
}
_STATUS_RANK = {"failed": 4, "healthy": 3, "observing": 2, "idle": 1, "unknown": 0}
_RELATION_TO_EDGE_KIND = {
    "manages": "solid",
    "uses": "soft",
    "reads": "dashed-long",
    "writes": "dashed-long",
    "routes_to": "dashed-anim",
    "exposes": "soft",
    "observes": "dashed-anim",
    "signals_to": "dashed-anim",
    "member_of": "soft",
}
_RELATION_LABELS = {
    "manages": "manages",
    "uses": "uses",
    "reads": "reads",
    "writes": "writes",
    "routes_to": "routes",
    "exposes": "exposes",
    "observes": "observes",
    "signals_to": "signals",
    "member_of": "member",
}
_RELATION_KEYS = {
    "observatory.niuu.world/manages": "manages",
    "observatory.niuu.world/uses": "uses",
    "observatory.niuu.world/reads": "reads",
    "observatory.niuu.world/writes": "writes",
    "observatory.niuu.world/routes-to": "routes_to",
    "observatory.niuu.world/exposes": "exposes",
    "observatory.niuu.world/observes": "observes",
    "observatory.niuu.world/signals-to": "signals_to",
    "observatory.niuu.world/member-of": "member_of",
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
        str(key): str(item) for key, item in value.items() if str(key).strip() and str(item).strip()
    }


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _edge(
    *,
    source_id: str,
    target_id: str,
    relation_type: str,
    source_adapter: str,
    evidence_field: str = "",
    confidence: str = "declared",
    label: str = "",
) -> ObservatoryEdge:
    edge_label = label or _RELATION_LABELS.get(relation_type, relation_type.replace("_", " "))
    edge_id = f"edge:{_slug(relation_type)}:{_slug(source_id)}:{_slug(target_id)}"
    evidence = {"adapter": source_adapter}
    if evidence_field:
        evidence["field"] = evidence_field
    return {
        "id": edge_id,
        "sourceId": source_id,
        "targetId": target_id,
        "kind": _RELATION_TO_EDGE_KIND.get(relation_type, "soft"),
        "relationType": relation_type,
        "label": edge_label,
        "confidence": confidence,
        "evidence": evidence,
    }


def _status_from_k8s(kind: str, payload: dict[str, Any]) -> str:
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    if kind in {"deployment", "statefulset", "replicaset"}:
        desired = int(status.get("replicas") or 0)
        ready = int(status.get("readyReplicas") or 0)
        available = int(status.get("availableReplicas") or 0)
        if desired == 0:
            return "unknown"
        return "healthy" if ready >= desired and available >= desired else "failed"
    if kind == "daemonset":
        desired = int(status.get("desiredNumberScheduled") or 0)
        ready = int(status.get("numberReady") or 0)
        if desired == 0:
            return "unknown"
        return "healthy" if ready >= desired else "failed"
    if kind == "pod":
        phase = str(status.get("phase") or "").lower()
        if phase == "running":
            return "healthy"
        if phase in {"failed", "unknown"}:
            return "failed"
        return "unknown"
    return "healthy"


def _status_from_session(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "running":
        return "healthy"
    if normalized in {"starting", "provisioning", "created"}:
        return "observing"
    if normalized == "failed":
        return "failed"
    if normalized in {"stopped", "archived"}:
        return "idle"
    return "unknown"


def _status_from_valkyrie(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"online", "healthy", "watching", "wakeful"}:
        return "healthy"
    if normalized in {"watch", "degraded", "observing"}:
        return "observing"
    if normalized in {"sleeping", "dreaming", "idle"}:
        return "idle"
    if normalized in {"offline", "failed"}:
        return "failed"
    return "unknown"


def _condition_status(payload: dict[str, Any], condition_type: str) -> bool:
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
    return any(
        isinstance(condition, dict)
        and str(condition.get("type") or "") == condition_type
        and str(condition.get("status") or "").lower() == "true"
        for condition in conditions
    )


def _merge_status(left: str, right: str) -> str:
    return left if _STATUS_RANK.get(left, 0) >= _STATUS_RANK.get(right, 0) else right


def _merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = {**left, **right}
    resources: list[Any] = []
    for value in (left.get("resources"), right.get("resources")):
        if isinstance(value, list):
            resources.extend(value)
    if resources:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[Any] = []
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            key = (
                str(resource.get("kind") or ""),
                str(resource.get("name") or ""),
                str(resource.get("uid") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(resource)
        merged["resources"] = deduped
    return merged


def _merge_discovered_entity(
    current: DiscoveredEntity | None,
    incoming: DiscoveredEntity,
) -> DiscoveredEntity:
    if current is None:
        return incoming
    kind = incoming.kind if incoming.kind != "service" else current.kind
    if current.kind == "warden" or incoming.kind == "warden":
        kind = "warden"
    name = incoming.name if incoming.name and incoming.name != incoming.id else current.name
    if current.kind == "warden" and current.name:
        name = current.name
    source_kinds = {
        item
        for value in (current.source_kind, incoming.source_kind)
        for item in str(value).split(",")
        if item
    }
    return DiscoveredEntity(
        id=current.id,
        kind=kind,
        name=name or current.name,
        cluster=incoming.cluster or current.cluster,
        namespace=incoming.namespace or current.namespace,
        status=_merge_status(current.status, incoming.status),
        parent_id=incoming.parent_id or current.parent_id,
        labels={**current.labels, **incoming.labels},
        annotations={**current.annotations, **incoming.annotations},
        source_adapter=(
            incoming.source_adapter
            if incoming.source_adapter == current.source_adapter
            else f"{current.source_adapter},{incoming.source_adapter}"
        ),
        source_kind=",".join(sorted(source_kinds)),
        source_uid=incoming.source_uid or current.source_uid,
        endpoints={**current.endpoints, **incoming.endpoints},
        metadata=_merge_metadata(current.metadata, incoming.metadata),
    )


def _merge_discovered_entities(entities: list[DiscoveredEntity]) -> list[DiscoveredEntity]:
    merged: dict[str, DiscoveredEntity] = {}
    for entity in entities:
        merged[entity.id] = _merge_discovered_entity(merged.get(entity.id), entity)
    return list(merged.values())


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
    # Placement. Every level is optional and they are not a fixed hierarchy: a
    # Kubernetes workload arrives with a cluster and a namespace, a resident on
    # a bare-metal Spark with a host and a realm and neither of the others.
    realm: str = ""
    cluster: str = ""
    namespace: str = ""
    host: str = ""
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
        edges: list[ObservatoryEdge] = []
        for warden in wardens:
            warden_id = str(getattr(warden, "id", "") or "").strip()
            if not warden_id:
                continue
            deployment_kwargs = getattr(warden, "deployment_kwargs", {}) or {}
            cluster = str(deployment_kwargs.get("cluster") or self._cluster)
            namespace = str(deployment_kwargs.get("namespace") or self._namespace)
            warden_node_id = (
                f"runtime:{_slug(cluster or 'local')}:"
                f"{_slug(namespace or 'default')}:warden:{_slug(warden_id)}"
            )
            entities.append(
                DiscoveredEntity(
                    id=warden_node_id,
                    kind="warden",
                    name=str(getattr(warden, "name", "") or warden_id),
                    cluster=cluster,
                    namespace=namespace,
                    status=(
                        "healthy"
                        if str(getattr(getattr(warden, "runtime", None), "state", "")).lower()
                        == "active"
                        else "unknown"
                    ),
                    source_adapter=self.__class__.__name__,
                    source_kind="wardenspec",
                    source_uid=warden_id,
                    metadata={"persona": str(getattr(warden, "persona", "") or "")},
                )
            )

            if cluster or namespace:
                edges.append(
                    _edge(
                        source_id=f"service:ravn@{cluster or 'local'}/{namespace or 'default'}",
                        target_id=warden_node_id,
                        relation_type="manages",
                        source_adapter=self.__class__.__name__,
                        evidence_field="WardenSpec.deployment",
                    )
                )

            mimir_binding = getattr(warden, "mimir", None)
            explicit_mimir = str(deployment_kwargs.get("mimir_entity") or "").strip()
            read_mounts = [
                str(item)
                for item in (
                    getattr(mimir_binding, "read_mount_names", None)
                    or getattr(mimir_binding, "mount_names", None)
                    or []
                )
                if str(item).strip()
            ]
            write_mounts = [
                str(item)
                for item in (
                    getattr(mimir_binding, "write_mount_names", None)
                    or (
                        [getattr(mimir_binding, "write_mount", "")]
                        if getattr(mimir_binding, "write_mount", "")
                        else []
                    )
                )
                if str(item).strip()
            ]
            for relation_type, mounts, field_name in (
                ("reads", read_mounts, "WardenSpec.mimir.read_mount_names"),
                ("writes", write_mounts, "WardenSpec.mimir.write_mount_names"),
            ):
                targets = [explicit_mimir] if explicit_mimir else []
                targets.extend(
                    f"mimir:{mount}@{cluster or 'local'}/{namespace or 'default'}"
                    for mount in mounts
                )
                for target in dict.fromkeys(targets):
                    if target:
                        edges.append(
                            _edge(
                                source_id=warden_node_id,
                                target_id=target,
                                relation_type=relation_type,
                                source_adapter=self.__class__.__name__,
                                evidence_field=field_name,
                            )
                        )
        return DiscoveryResult(entities=entities, edges=edges)


class StaticRelationshipDiscoveryAdapter:
    """Emit declarative relationships from Observatory configuration."""

    def __init__(self, relationships: list[dict[str, Any]] | None = None) -> None:
        self._relationships = relationships or []

    async def discover(self) -> DiscoveryResult:
        edges: list[ObservatoryEdge] = []
        for item in self._relationships:
            if not isinstance(item, dict):
                continue
            source = str(item.get("sourceId") or item.get("source") or "").strip()
            target = str(item.get("targetId") or item.get("target") or "").strip()
            relation_type = str(item.get("relationType") or item.get("relation_type") or "").strip()
            if not source or not target or not relation_type:
                continue
            edge = _edge(
                source_id=source,
                target_id=target,
                relation_type=relation_type,
                source_adapter=self.__class__.__name__,
                evidence_field=str(item.get("evidence") or "observatory.discovery.relationships"),
                confidence=str(item.get("confidence") or "declared"),
                label=str(item.get("label") or ""),
            )
            if item.get("id"):
                edge["id"] = str(item["id"])
            if item.get("kind"):
                edge["kind"] = str(item["kind"])
            edges.append(edge)
        return DiscoveryResult(edges=edges)


class VolundrSessionsDiscoveryAdapter:
    """Discover live Volundr sessions as first-class Observatory entities."""

    def __init__(
        self,
        base_url: str,
        cluster: str = "",
        namespace: str = "skuld",
        volundr_namespace: str = "volundr",
        status_filter: str = "running",
        timeout_seconds: float = 5.0,
        headers: dict[str, str] | None = None,
        auth_header_env: str = "",
        include_manager_edge: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cluster = cluster
        self._namespace = namespace
        self._volundr_namespace = volundr_namespace
        self._status_filter = status_filter
        self._timeout_seconds = timeout_seconds
        self._headers = dict(headers or {})
        self._auth_header_env = auth_header_env
        self._include_manager_edge = include_manager_edge
        self._transport = transport

    async def discover(self) -> DiscoveryResult:
        headers = dict(self._headers)
        if self._auth_header_env:
            token = os.environ.get(self._auth_header_env, "").strip()
            if token:
                headers.setdefault("Authorization", token)
        params = {"status": self._status_filter} if self._status_filter else None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._sessions_url(),
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return DiscoveryResult(events=[_adapter_warning("volundr-sessions", str(exc))])
        if not isinstance(payload, list):
            return DiscoveryResult()

        entities: list[DiscoveredEntity] = []
        edges: list[ObservatoryEdge] = []
        for session in payload:
            if not isinstance(session, dict):
                continue
            session_id = str(session.get("id") or "").strip()
            if not session_id:
                continue
            cluster = self._cluster or str(session.get("cluster") or "")
            namespace = self._namespace or "skuld"
            entity_id = (
                f"runtime:{_slug(cluster or 'local')}:{_slug(namespace)}:skuld:{_slug(session_id)}"
            )
            endpoints = {
                key: str(value)
                for key, value in {
                    "chat": session.get("chat_endpoint"),
                    "code": session.get("code_endpoint"),
                    "a2a": session.get("a2aEndpointUrl") or session.get("a2a_endpoint_url"),
                    "a2aCard": session.get("a2aCardUrl") or session.get("a2a_card_url"),
                }.items()
                if value
            }
            entities.append(
                DiscoveredEntity(
                    id=entity_id,
                    kind="skuld",
                    name=str(session.get("name") or session_id[:8]),
                    cluster=cluster,
                    namespace=namespace,
                    status=_status_from_session(str(session.get("status") or "")),
                    source_adapter=self.__class__.__name__,
                    source_kind="volundr-session",
                    source_uid=session_id,
                    endpoints=endpoints,
                    metadata={
                        "sessionId": session_id,
                        "model": str(session.get("model") or ""),
                        "tokens": int(session.get("tokens_used") or 0),
                        "ownerId": str(session.get("owner_id") or ""),
                        "tenantId": str(session.get("tenant_id") or ""),
                        "activity": str(session.get("activity_state") or ""),
                        "createdAt": str(session.get("created_at") or ""),
                        "lastActive": str(session.get("last_active") or ""),
                        "workloadType": str(session.get("workload_type") or "session"),
                        "agentKind": "workflow-session",
                        "visibility": str(
                            session.get("a2aVisibility") or session.get("a2a_visibility") or "user"
                        ),
                        "environmentId": str(
                            session.get("environmentId") or session.get("environment_id") or ""
                        ),
                    },
                )
            )
            if self._include_manager_edge and cluster:
                edges.append(
                    _edge(
                        source_id=f"volundr:volundr@{cluster}/{self._volundr_namespace}",
                        target_id=entity_id,
                        relation_type="manages",
                        source_adapter=self.__class__.__name__,
                        evidence_field="GET /api/v1/forge/sessions",
                        confidence="observed",
                    )
                )
        return DiscoveryResult(entities=entities, edges=edges)

    def _sessions_url(self) -> str:
        if self._base_url.endswith("/api/v1/forge"):
            return f"{self._base_url}/sessions"
        return f"{self._base_url}/api/v1/forge/sessions"


class FluxHelmReleaseSessionDiscoveryAdapter:
    """Discover Volundr-managed Skuld sessions from Flux HelmRelease resources."""

    def __init__(
        self,
        cluster: str = "",
        namespace: str = "skuld",
        volundr_namespace: str = "volundr",
        label_selector: str = "app.kubernetes.io/managed-by=volundr",
        name_prefix: str = "skuld-",
        image_tags: list[str] | None = None,
        timeout_seconds: float = 10.0,
        service_account_root: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cluster = cluster
        self._namespace = namespace
        self._volundr_namespace = volundr_namespace
        self._label_selector = label_selector
        self._name_prefix = name_prefix
        self._image_tags = {str(item) for item in image_tags or [] if str(item)}
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
                        "flux-sessions",
                        "Kubernetes service account token not mounted",
                    )
                ]
            )

        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        base_url = f"https://{host}:{port}"
        headers = {"Authorization": f"Bearer {token_path.read_text(encoding='utf-8').strip()}"}
        params = {"labelSelector": self._label_selector} if self._label_selector else None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                verify=str(ca_path) if ca_path.exists() else True,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{base_url}{self._path()}",
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return DiscoveryResult(events=[_adapter_warning("flux-sessions", str(exc))])

        entities: list[DiscoveredEntity] = []
        edges: list[ObservatoryEdge] = []
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or not self._include_helmrelease(item):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            values = (
                item.get("spec", {}).get("values", {}) if isinstance(item.get("spec"), dict) else {}
            )
            session_values = (
                values.get("session", {}) if isinstance(values.get("session"), dict) else {}
            )
            image_values = values.get("image", {}) if isinstance(values.get("image"), dict) else {}
            session_id = str(session_values.get("id") or "").strip()
            if not session_id:
                name = str(metadata.get("name") or "")
                session_id = name.removeprefix(self._name_prefix)
            if not session_id:
                continue
            entity_id = (
                f"runtime:{_slug(self._cluster or 'local')}:"
                f"{_slug(self._namespace)}:skuld:{_slug(session_id)}"
            )
            endpoints = {
                key: str(value)
                for key, value in {
                    "a2a": session_values.get("a2aEndpointUrl"),
                    "a2aCard": session_values.get("a2aCardUrl"),
                }.items()
                if value
            }
            entities.append(
                DiscoveredEntity(
                    id=entity_id,
                    kind="skuld",
                    name=str(session_values.get("name") or session_id[:8]),
                    cluster=self._cluster,
                    namespace=self._namespace,
                    status="healthy",
                    source_adapter=self.__class__.__name__,
                    source_kind="flux-helmrelease",
                    source_uid=str(metadata.get("uid") or ""),
                    endpoints=endpoints,
                    metadata={
                        "sessionId": session_id,
                        "model": str(session_values.get("model") or ""),
                        "imageTag": str(image_values.get("tag") or ""),
                        "ownerId": str(session_values.get("ownerId") or ""),
                        "tenantId": str(session_values.get("tenantId") or ""),
                        "visibility": str(session_values.get("a2aVisibility") or "user"),
                        "environmentId": str(session_values.get("environmentId") or ""),
                        "resources": [
                            {
                                "kind": "helmrelease",
                                "name": str(metadata.get("name") or ""),
                                "uid": str(metadata.get("uid") or ""),
                                "generation": metadata.get("generation"),
                            }
                        ],
                    },
                )
            )
            if self._cluster:
                edges.append(
                    _edge(
                        source_id=f"volundr:volundr@{self._cluster}/{self._volundr_namespace}",
                        target_id=entity_id,
                        relation_type="manages",
                        source_adapter=self.__class__.__name__,
                        evidence_field="HelmRelease.spec.values.session",
                        confidence="observed",
                    )
                )
        return DiscoveryResult(entities=entities, edges=edges)

    def _path(self) -> str:
        namespace = quote(self._namespace, safe="")
        return f"/apis/helm.toolkit.fluxcd.io/v2/namespaces/{namespace}/helmreleases"

    def _include_helmrelease(self, item: dict[str, Any]) -> bool:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = str(metadata.get("name") or "")
        if self._name_prefix and not name.startswith(self._name_prefix):
            return False
        if not _condition_status(item, "Ready"):
            return False
        values = (
            item.get("spec", {}).get("values", {}) if isinstance(item.get("spec"), dict) else {}
        )
        image = values.get("image", {}) if isinstance(values.get("image"), dict) else {}
        image_tag = str(image.get("tag") or "")
        return not self._image_tags or image_tag in self._image_tags


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


class RavnValkyrieDiscoveryAdapter:
    """Discover cross-cluster Valkyries from Ravn's live dashboard projection."""

    def __init__(
        self,
        base_url: str,
        namespace: str = "nats",
        timeout_seconds: float = 5.0,
        auth_adapter: str = "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter",
        auth_kwargs: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._namespace = namespace
        self._timeout_seconds = timeout_seconds
        self._auth: HttpAuthPort = import_class(auth_adapter)(**(auth_kwargs or {}))
        self._transport = transport

    async def discover(self) -> DiscoveryResult:
        try:
            headers = await asyncio.to_thread(self._auth.headers)
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/api/v1/ravn/valkyrie/dashboard",
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return DiscoveryResult(
                events=[_adapter_warning("ravn-valkyrie", f"{self._base_url}: {exc}")]
            )

        if not isinstance(payload, dict):
            return DiscoveryResult()

        environments = {
            str(item.get("id") or ""): item
            for item in payload.get("environments", [])
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        entities: list[DiscoveredEntity] = []
        for item in payload.get("valkyries", []):
            if not isinstance(item, dict):
                continue
            valkyrie_id = str(item.get("id") or "").strip()
            environment_id = str(item.get("environmentId") or "").strip()
            if not valkyrie_id or not environment_id:
                continue
            environment = environments.get(environment_id, {})
            topology_cluster = environment_id.removeprefix("env-k8s-") or environment_id
            entities.append(
                DiscoveredEntity(
                    id=(
                        f"runtime:{_slug(topology_cluster)}:{_slug(self._namespace)}:"
                        f"valkyrie:{_slug(valkyrie_id)}"
                    ),
                    kind="valkyrie",
                    name=str(item.get("name") or valkyrie_id),
                    cluster=topology_cluster,
                    namespace=self._namespace,
                    status=_status_from_valkyrie(str(item.get("status") or "")),
                    source_adapter=self.__class__.__name__,
                    source_kind="ravn:valkyrie-dashboard",
                    source_uid=valkyrie_id,
                    metadata={
                        "ravnEnvironmentId": environment_id,
                        "environmentHealth": str(environment.get("health") or ""),
                        "persona": str(item.get("persona") or ""),
                        "specialty": str(item.get("specialty") or ""),
                        "autonomy": str(item.get("autonomyMode") or ""),
                        "wakefulness": str(item.get("wakefulness") or ""),
                        "flockId": str(item.get("flockId") or ""),
                        "confidence": item.get("confidence"),
                    },
                )
            )
        return DiscoveryResult(entities=entities)


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
        self._include_kinds = include_kinds or [
            "deployments",
            "statefulsets",
            "daemonsets",
            "services",
            "pods",
            "configmaps",
            "persistentvolumeclaims",
            "ingresses",
        ]
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
        edges: list[ObservatoryEdge] = []
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
                            resource_kind = _singular_resource_kind(kind)
                            entity = self._entity_from_k8s(resource_kind, item)
                            if entity is not None:
                                entities.append(entity)
                                edges.extend(
                                    _relationships_from_k8s(
                                        entity,
                                        resource_kind=resource_kind,
                                        item=item,
                                        source_adapter=self.__class__.__name__,
                                    )
                                )
        except Exception as exc:
            events.append(_adapter_warning("kubernetes", str(exc)))
        return DiscoveryResult(
            entities=_merge_discovered_entities(entities),
            edges=edges,
            events=events,
        )

    def _path_for_kind(self, kind: str) -> str:
        namespace = quote(self._namespace, safe="")
        if kind in {"deployments", "statefulsets", "daemonsets", "replicasets"}:
            if namespace:
                return f"/apis/apps/v1/namespaces/{namespace}/{kind}"
            return f"/apis/apps/v1/{kind}"
        if kind in {"services", "pods", "configmaps", "persistentvolumeclaims"}:
            if namespace:
                return f"/api/v1/namespaces/{namespace}/{kind}"
            return f"/api/v1/{kind}"
        if kind == "ingresses":
            if namespace:
                return f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses"
            return "/apis/networking.k8s.io/v1/ingresses"
        if kind == "httproutes":
            if namespace:
                return f"/apis/gateway.networking.k8s.io/v1/namespaces/{namespace}/httproutes"
            return "/apis/gateway.networking.k8s.io/v1/httproutes"
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
        cluster_label = labels.get("niuu.world/cluster") or ""
        cluster = (
            self._cluster if cluster_label.lower() in {"", "unknown"} else cluster_label
        ) or "unknown"
        component = (
            labels.get("niuu.world/kind")
            or labels.get("observatory.niuu.world/type")
            or labels.get("app.kubernetes.io/component")
        )
        app_name = labels.get("app.kubernetes.io/name")
        type_id = _type_id_for_component(component or "", app_name or "")
        logical_name = labels.get("niuu.world/entity-id") or labels.get("niuu.world/service-id")
        display_name = labels.get("niuu.world/display-name") or ""
        if labels.get("niuu.world/warden-id"):
            logical_name = labels["niuu.world/warden-id"]
            display_name = (
                annotations.get("niuu.world/warden-name")
                or labels.get("niuu.world/warden-name")
                or display_name
            )
            type_id = "warden"
        elif logical_name:
            pass
        elif app_name:
            logical_name = app_name
        elif component:
            logical_name = component
        else:
            logical_name = name
        display_name = display_name or logical_name
        entity_id = (
            f"runtime:{_slug(cluster)}:{_slug(namespace)}:{_slug(type_id)}:{_slug(logical_name)}"
        )
        entity_metadata: dict[str, Any] = {
            "component": component or "",
            "app": app_name or "",
            "resources": [
                {
                    "kind": resource_kind,
                    "name": name,
                    "uid": str(metadata.get("uid") or ""),
                    "generation": metadata.get("generation"),
                }
            ],
        }
        visibility = annotations.get("observatory.niuu.world/a2a-visibility")
        if visibility:
            entity_metadata["visibility"] = visibility
        return DiscoveredEntity(
            id=entity_id,
            kind=type_id if type_id in _SEED_TYPE_IDS else "service",
            name=display_name,
            cluster=cluster,
            namespace=namespace,
            status=_status_from_k8s(resource_kind, item),
            labels=labels,
            annotations=annotations,
            source_adapter=self.__class__.__name__,
            source_kind="kubernetes",
            source_uid=str(metadata.get("uid") or ""),
            endpoints=_endpoints_for_k8s(resource_kind, item, labels, annotations),
            metadata=entity_metadata,
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
                entities_by_id[entity.id] = _merge_discovered_entity(
                    entities_by_id.get(entity.id),
                    entity,
                )
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


def topology_from_discovery(
    result: DiscoveryResult,
    *,
    known_type_ids: Collection[str] | None = None,
) -> ObservatorySnapshot:
    """Materialize an Observatory topology from canonical discovered entities.

    ``known_type_ids`` should come from the live registry, which is the
    configurable source of truth for entity types. An entity whose kind is not
    registered still renders — as a ``service`` — but says so in an event, so a
    missing type is visible to an operator instead of silently flattening the
    graph. Omit it to fall back to the registry seed.
    """
    type_ids = frozenset(known_type_ids) if known_type_ids is not None else _SEED_TYPE_IDS
    unregistered: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, ObservatoryEdge] = {}

    # An adapter may discover a realm or a host as an entity in its own right.
    # Prefer that real node over a synthesised container so the same host does
    # not appear twice under two different ids.
    declared_realms = _declared_containers(result.entities, "realm")
    declared_hosts = _declared_containers(result.entities, "host")

    for entity in sorted(
        result.entities,
        key=lambda item: (
            item.realm,
            item.cluster,
            item.namespace,
            item.host,
            item.kind,
            item.name,
        ),
    ):
        realm_id = declared_realms.get(entity.realm) or _realm_id(entity.realm)
        if entity.realm and entity.realm not in declared_realms:
            nodes.setdefault(
                realm_id,
                {
                    "id": realm_id,
                    "typeId": "realm",
                    "label": entity.realm,
                    "parentId": None,
                    "status": "healthy",
                    "sourceKind": "discovery",
                    "layoutHints": {"mode": "pack", "scope": "world", "packGroup": "realm"},
                },
            )

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
            # Filled in rather than set at creation: whichever entity mentions
            # the cluster first may not be the one that knows its realm.
            if entity.realm and not nodes[cluster_id].get("parentId"):
                nodes[cluster_id]["parentId"] = realm_id

        namespace_id = _namespace_id(entity.cluster, entity.namespace)
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

        host_id = declared_hosts.get(entity.host) or _host_id(entity.cluster, entity.host)
        if entity.host and entity.host not in declared_hosts:
            nodes.setdefault(
                host_id,
                {
                    "id": host_id,
                    "typeId": "host",
                    "label": entity.host,
                    "parentId": cluster_id
                    if entity.cluster
                    else realm_id
                    if entity.realm
                    else None,
                    "status": "unknown",
                    "sourceKind": "discovery",
                    "clusterName": entity.cluster,
                    "layoutHints": {"mode": "pack", "scope": "cluster", "packGroup": "host"},
                },
            )

        parent_id = entity.parent_id or _innermost_container(
            entity,
            namespace_id=namespace_id,
            host_id=host_id,
            cluster_id=cluster_id,
            realm_id=realm_id,
        )

        if parent_id == entity.id:
            parent_id = None

        if entity.kind and entity.kind not in type_ids:
            unregistered.add(entity.kind)
        node = _entity_to_node(entity, parent_id=parent_id, known_type_ids=type_ids)
        nodes[node["id"]] = node

    for edge in result.edges:
        resolved = _resolve_edge(edge, nodes)
        if resolved and resolved["sourceId"] != resolved["targetId"]:
            edges[resolved["id"]] = resolved

    events = list(result.events)
    if unregistered:
        # Rendering an unknown kind as `service` keeps the graph usable, but
        # doing it quietly is how realms and models disappeared for months.
        kinds = ", ".join(sorted(unregistered))
        events.append(
            {
                "id": f"observatory:registry:unregistered:{_slug(kinds)}",
                "type": "warning",
                "level": "warning",
                "service": "observatory",
                "subject": "registry",
                "body": f"Entity types not registered, rendered as service: {kinds}",
                "message": f"Entity types not registered, rendered as service: {kinds}",
                "timestamp": _iso(),
            }
        )

    node_list = list(nodes.values())
    edge_list = list(edges.values())
    return {
        "timestamp": _iso(),
        "revision": _revision(node_list, edge_list, events),
        "nodes": node_list,
        "edges": edge_list,
        "events": events,
        "layoutHints": {"mode": "pack", "scope": "world"},
    }


def _revision(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    events: list[Mapping[str, Any]],
) -> str:
    """Stable digest of graph content, ignoring when it was materialized.

    Only event *ids* participate, not whole events: adapters stamp their events
    with a fresh timestamp on every poll, so digesting them whole would make the
    revision change even when nothing about the topology did.
    """
    payload = json.dumps(
        {
            "nodes": sorted(nodes, key=lambda node: str(node.get("id", ""))),
            "edges": sorted(edges, key=lambda edge: str(edge.get("id", ""))),
            "events": sorted(str(event.get("id", "")) for event in events),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _resolve_edge(
    edge: ObservatoryEdge,
    nodes: dict[str, dict[str, Any]],
) -> ObservatoryEdge | None:
    source_id = _resolve_node_ref(str(edge.get("sourceId") or ""), nodes)
    target_id = _resolve_node_ref(str(edge.get("targetId") or ""), nodes)
    if not source_id or not target_id:
        return None
    relation_type = str(edge.get("relationType") or "")
    resolved: ObservatoryEdge = {
        **edge,
        "id": str(edge.get("id") or f"edge:{_slug(source_id)}:{_slug(target_id)}"),
        "sourceId": source_id,
        "targetId": target_id,
        "kind": str(edge.get("kind") or _RELATION_TO_EDGE_KIND.get(relation_type, "soft")),
    }
    if relation_type and not resolved.get("label"):
        resolved["label"] = _RELATION_LABELS.get(relation_type, relation_type.replace("_", " "))
    if not resolved.get("confidence") and relation_type:
        resolved["confidence"] = "declared"
    return resolved


def _resolve_node_ref(ref: str, nodes: dict[str, dict[str, Any]]) -> str:
    value = ref.strip()
    if not value:
        return ""
    if value in nodes:
        return value

    node_list = list(nodes.values())
    if "@" in value:
        value, scope = value.split("@", 1)
        cluster, _, namespace = scope.partition("/")
    else:
        cluster = ""
        namespace = ""

    if ":" in value:
        type_id, _, name = value.partition(":")
    else:
        type_id = ""
        name = value
    type_id = _type_id_for_component(type_id, "") if type_id else ""
    name_slug = _slug(name)

    candidates: list[str] = []
    for node in node_list:
        if type_id and node.get("typeId") != type_id:
            continue
        if cluster and node.get("clusterName") != cluster:
            continue
        if namespace and node.get("namespace") != namespace:
            continue
        node_id = str(node.get("id") or "")
        label = str(node.get("label") or "")
        labels = node.get("labels") if isinstance(node.get("labels"), dict) else {}
        logical_names = {
            _slug(label),
            _slug(node_id.rsplit(":", 1)[-1]),
            _slug(str(labels.get("app.kubernetes.io/name") or "")),
            _slug(str(labels.get("app.kubernetes.io/component") or "")),
            _slug(str(labels.get("niuu.world/entity-id") or "")),
            _slug(str(labels.get("niuu.world/service-id") or "")),
        }
        if name_slug in logical_names or any(
            logical_name.endswith(f"-{name_slug}") for logical_name in logical_names
        ):
            candidates.append(node_id)
    return candidates[0] if len(set(candidates)) == 1 else ""


def _entity_to_node(
    entity: DiscoveredEntity,
    *,
    parent_id: str | None,
    known_type_ids: Collection[str],
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": entity.id,
        "typeId": entity.kind if entity.kind in known_type_ids else "service",
        "label": entity.name,
        "parentId": parent_id,
        "status": entity.status,
        "sourceKind": entity.source_kind,
        "sourceId": entity.source_uid or entity.id,
        # Placement carried as names, matching `clusterName`/`namespace`. Empty
        # is meaningful: it says this entity genuinely has no such container.
        "realm": entity.realm,
        "clusterName": entity.cluster,
        "namespace": entity.namespace,
        "host": entity.host,
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


def _realm_id(realm: str) -> str:
    return f"realm-{_slug(realm or 'unknown')}"


def _host_id(cluster: str, host: str) -> str:
    """Host ids are cluster-scoped: two clusters may both have a `node-1`.

    A host outside any cluster is keyed on its name alone, which is what makes
    a bare-metal box addressable without inventing a cluster for it.
    """
    if not cluster:
        return f"host-{_slug(host or 'unknown')}"
    return f"host-{_slug(cluster)}-{_slug(host or 'unknown')}"


def _declared_containers(entities: Iterable[DiscoveredEntity], kind: str) -> dict[str, str]:
    """Map container name to node id for entities that declare themselves one."""
    return {entity.name: entity.id for entity in entities if entity.kind == kind and entity.name}


def _innermost_container(
    entity: DiscoveredEntity,
    *,
    namespace_id: str,
    host_id: str,
    cluster_id: str,
    realm_id: str,
) -> str | None:
    """Pick the tightest container that actually applies to this entity.

    Namespace wins over host for a Kubernetes workload because that is the
    containment the graph is drawn around; the host stays a sibling under the
    cluster. Outside Kubernetes there is no namespace, so the host becomes the
    container — and an entity with no placement at all stays top-level rather
    than being forced under a fabricated cluster.
    """
    if entity.cluster and entity.namespace:
        return namespace_id
    if entity.host:
        return host_id
    if entity.cluster:
        return cluster_id
    if entity.realm:
        return realm_id
    return None


def _singular_resource_kind(kind: str) -> str:
    return {
        "deployments": "deployment",
        "statefulsets": "statefulset",
        "daemonsets": "daemonset",
        "replicasets": "replicaset",
        "services": "service",
        "pods": "pod",
        "configmaps": "configmap",
        "persistentvolumeclaims": "persistentvolumeclaim",
        "ingresses": "ingress",
        "httproutes": "httproute",
    }.get(kind, kind.rstrip("s"))


def _type_id_for_component(component: str, app_name: str = "") -> str:
    normalized = _slug(component)
    if normalized in _SEED_TYPE_IDS:
        return normalized
    mapped = _COMPONENT_TYPES.get(component) or _COMPONENT_TYPES.get(normalized)
    if mapped:
        return mapped
    return _COMPONENT_TYPES.get(app_name, "service")


def _is_edge(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("sourceId"), str)
        and isinstance(value.get("targetId"), str)
        and isinstance(value.get("kind"), str)
    )


def _relationships_from_k8s(
    entity: DiscoveredEntity,
    *,
    resource_kind: str,
    item: dict[str, Any],
    source_adapter: str,
) -> list[ObservatoryEdge]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    labels = _clean_map(metadata.get("labels"))
    annotations = _clean_map(metadata.get("annotations"))
    edges: list[ObservatoryEdge] = []
    for source, source_name in ((labels, "label"), (annotations, "annotation")):
        for key, relation_type in _RELATION_KEYS.items():
            value = source.get(key, "")
            for target in _csv(value):
                edges.append(
                    _edge(
                        source_id=entity.id,
                        target_id=target,
                        relation_type=relation_type,
                        source_adapter=source_adapter,
                        evidence_field=f"metadata.{source_name}s[{key}]",
                    )
                )

    if resource_kind == "ingress":
        edges.extend(
            _ingress_relationship_edges(
                entity,
                item=item,
                source_adapter=source_adapter,
            )
        )
    if resource_kind == "httproute":
        edges.extend(
            _httproute_relationship_edges(
                entity,
                item=item,
                source_adapter=source_adapter,
            )
        )
    return edges


def _ingress_relationship_edges(
    entity: DiscoveredEntity,
    *,
    item: dict[str, Any],
    source_adapter: str,
) -> list[ObservatoryEdge]:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    targets: list[str] = []
    default_backend = (
        spec.get("defaultBackend") if isinstance(spec.get("defaultBackend"), dict) else {}
    )
    service = (
        default_backend.get("service") if isinstance(default_backend.get("service"), dict) else {}
    )
    if service.get("name"):
        targets.append(str(service["name"]))
    rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        http = rule.get("http") if isinstance(rule.get("http"), dict) else {}
        paths = http.get("paths") if isinstance(http.get("paths"), list) else []
        for path in paths:
            if not isinstance(path, dict):
                continue
            backend = path.get("backend") if isinstance(path.get("backend"), dict) else {}
            service = backend.get("service") if isinstance(backend.get("service"), dict) else {}
            if service.get("name"):
                targets.append(str(service["name"]))
    return [
        _edge(
            source_id=entity.id,
            target_id=f"service:{target}@{entity.cluster}/{entity.namespace}",
            relation_type="routes_to",
            source_adapter=source_adapter,
            evidence_field="spec.rules[].http.paths[].backend.service.name",
            confidence="observed",
        )
        for target in dict.fromkeys(targets)
    ]


def _httproute_relationship_edges(
    entity: DiscoveredEntity,
    *,
    item: dict[str, Any],
    source_adapter: str,
) -> list[ObservatoryEdge]:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
    targets: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        refs = rule.get("backendRefs") if isinstance(rule.get("backendRefs"), list) else []
        for ref in refs:
            if (
                isinstance(ref, dict)
                and str(ref.get("kind") or "Service") == "Service"
                and ref.get("name")
            ):
                targets.append(str(ref["name"]))
    return [
        _edge(
            source_id=entity.id,
            target_id=f"service:{target}@{entity.cluster}/{entity.namespace}",
            relation_type="routes_to",
            source_adapter=source_adapter,
            evidence_field="spec.rules[].backendRefs[].name",
            confidence="observed",
        )
        for target in dict.fromkeys(targets)
    ]


def _endpoints_for_k8s(
    resource_kind: str,
    item: dict[str, Any],
    labels: dict[str, str],
    annotations: dict[str, str],
) -> dict[str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    namespace = str(metadata.get("namespace") or labels.get("niuu.world/namespace") or "")
    name = str(metadata.get("name") or "")
    endpoints: dict[str, str] = {}
    public = labels.get("niuu.world/public-url")
    if public:
        endpoints["public"] = public
    a2a_card = annotations.get("observatory.niuu.world/a2a-card-url")
    if a2a_card:
        endpoints["a2aCard"] = a2a_card
    if resource_kind == "service" and namespace and name:
        endpoints["internal"] = f"http://{name}.{namespace}.svc.cluster.local"
    if resource_kind == "ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        hosts = [
            str(rule.get("host")) for rule in rules if isinstance(rule, dict) and rule.get("host")
        ]
        if hosts:
            endpoints["public"] = f"https://{hosts[0]}"
    return endpoints
