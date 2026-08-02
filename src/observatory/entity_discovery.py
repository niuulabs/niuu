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
from urllib.parse import quote, urlsplit

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


class _HttpServiceDiscoveryAdapter:
    """Shared plumbing for adapters that read one niuu service over HTTP.

    Each subclass supplies `collect`; failures become a warning event rather
    than an exception, because one unreachable service must not empty the graph
    the other adapters contributed to.
    """

    warning_name = "service"

    def __init__(
        self,
        base_url: str,
        cluster: str = "",
        realm: str = "",
        namespace: str = "",
        timeout_seconds: float = 15.0,
        auth_adapter: str = "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter",
        auth_kwargs: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cluster = cluster
        self._realm = realm
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
                return await self.collect(client, headers)
        except Exception as exc:
            # Some httpx failures (ReadTimeout in particular) stringify to "",
            # which produced a warning naming only the URL and no fault.
            detail = str(exc) or type(exc).__name__
            return DiscoveryResult(
                events=[_adapter_warning(self.warning_name, f"{self._base_url}: {detail}")]
            )

    async def collect(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> DiscoveryResult:
        raise NotImplementedError

    async def _json(self, client: httpx.AsyncClient, headers: dict[str, str], path: str) -> Any:
        response = await client.get(f"{self._base_url}{path}", headers=headers)
        response.raise_for_status()
        return response.json()

    def _entity(self, kind: str, name: str, entity_id: str, **kwargs: Any) -> DiscoveredEntity:
        return DiscoveredEntity(
            id=entity_id,
            kind=kind,
            name=name,
            realm=self._realm,
            cluster=self._cluster,
            namespace=self._namespace,
            source_adapter=self.__class__.__name__,
            **kwargs,
        )


class BifrostCatalogDiscoveryAdapter(_HttpServiceDiscoveryAdapter):
    """Discover the real model catalogue from Bifröst.

    This replaces the three model children the Guild used to fabricate for
    every Bifröst (Anthropic, OpenAI, Local) from a hardcoded list. Models here
    are the ones actually configured, and each is attributed to the provider
    that serves it — which is also what makes local-vs-hosted visible.
    """

    warning_name = "bifrost"

    async def collect(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> DiscoveryResult:
        models = await self._json(client, headers, "/api/v1/bifrost/models")
        providers = await self._json(client, headers, "/api/v1/bifrost/providers")
        providers = providers if isinstance(providers, list) else []
        models = models if isinstance(models, list) else []

        gateway_id = f"bifrost:{_slug(self._cluster or 'unknown')}"
        entities = [
            self._entity(
                "bifrost",
                "Bifröst",
                gateway_id,
                status="healthy",
                endpoints={"internal": self._base_url},
                metadata={
                    "providers": len(providers),
                    "models": len(models),
                    "vendors": sorted(
                        {
                            str(p.get("vendor") or "")
                            for p in providers
                            if isinstance(p, dict) and p.get("vendor")
                        }
                    ),
                },
            )
        ]
        edges: list[ObservatoryEdge] = []

        # A provider's base_url is real configuration, so "this model is served
        # from here" is observed rather than guessed.
        served_by = {
            str(model_id): provider
            for provider in providers
            if isinstance(provider, dict)
            for model_id in (provider.get("model_ids") or [])
        }

        for model in models:
            if not isinstance(model, dict) or not model.get("id"):
                continue
            model_id = str(model["id"])
            provider = served_by.get(model_id) or {}
            base_url = str(provider.get("base_url") or "")
            node_id = f"model:{_slug(self._cluster or 'unknown')}:{_slug(model_id)}"
            entities.append(
                self._entity(
                    "model",
                    str(model.get("name") or model_id),
                    node_id,
                    parent_id=gateway_id,
                    status="healthy" if model.get("enabled", True) else "idle",
                    metadata={
                        "modelId": model_id,
                        "vendor": str(model.get("vendor") or ""),
                        "provider": str(model.get("provider") or provider.get("key") or ""),
                        "tier": str(model.get("tier") or ""),
                        "location": _model_location(base_url, provider),
                        "supportsTools": bool(model.get("supports_tools")),
                        "supportsThinking": bool(model.get("supports_thinking")),
                        "vramRequired": model.get("vram_required"),
                        "costPerMillionTokens": model.get("cost_per_million_tokens"),
                    },
                )
            )
            if provider:
                edges.append(
                    _edge(
                        source_id=gateway_id,
                        target_id=node_id,
                        relation_type="routes_to",
                        source_adapter=self.__class__.__name__,
                        evidence_field="model_ids",
                        confidence="observed",
                    )
                )

        return DiscoveryResult(entities=entities, edges=edges)


class RavnResidentsDiscoveryAdapter(_HttpServiceDiscoveryAdapter):
    """Discover long-running residents from Ravn's fleet projection.

    This is also how residents outside Kubernetes reach the graph when the
    Ravn that knows about them is reachable: the projection already carries
    local and container deployments, not only cluster ones.
    """

    warning_name = "ravn-residents"

    async def collect(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> DiscoveryResult:
        payload = await self._json(client, headers, "/api/v1/ravn/ravens")
        ravens = payload if isinstance(payload, list) else []
        entities: list[DiscoveredEntity] = []
        edges: list[ObservatoryEdge] = []

        for raven in ravens:
            if not isinstance(raven, dict) or not raven.get("id"):
                continue
            raven_id = str(raven["id"])
            name = str(raven.get("resident_name") or raven.get("persona_name") or raven_id)
            node_id = f"ravn:{_slug(self._cluster or 'unknown')}:{_slug(raven_id)}"
            flock_id = str(raven.get("flock_id") or "")
            entities.append(
                self._entity(
                    "ravn_long",
                    name,
                    node_id,
                    # A resident reports where it runs; a local or container
                    # deployment has no cluster placement to inherit.
                    host=str(raven.get("location") or ""),
                    status=_status_from_valkyrie(str(raven.get("status") or "")),
                    endpoints=(
                        {"chat": str(raven["chat_endpoint"])} if raven.get("chat_endpoint") else {}
                    ),
                    metadata={
                        "persona": str(raven.get("persona_name") or ""),
                        "model": str(raven.get("model") or ""),
                        "deployment": str(raven.get("deployment") or raven.get("backend") or ""),
                        "engine": str(raven.get("engine") or ""),
                        "flockId": flock_id,
                        "flockRole": str(raven.get("flock_role") or ""),
                        "peerId": str(raven.get("peer_id") or ""),
                        "desiredState": raven.get("desired_state"),
                        "observedState": raven.get("observed_state"),
                    },
                )
            )
            if flock_id:
                # Flock membership is declared by the resident's own config,
                # which is what makes a mesh visible as more than co-location.
                edges.append(
                    _edge(
                        source_id=node_id,
                        target_id=f"flock:{_slug(flock_id)}",
                        relation_type="member_of",
                        source_adapter=self.__class__.__name__,
                        evidence_field="flock_id",
                        confidence="observed",
                    )
                )

        return DiscoveryResult(entities=entities, edges=edges)


class TingWorkDiscoveryAdapter(_HttpServiceDiscoveryAdapter):
    """Discover the dispatcher and what it currently has in flight."""

    warning_name = "ting"

    async def collect(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> DiscoveryResult:
        summary = await self._json(client, headers, "/api/v1/ting/runs/summary")
        counts = {str(k): int(v) for k, v in summary.items()} if isinstance(summary, dict) else {}
        active = sum(count for state, count in counts.items() if state.lower() == "running")
        return DiscoveryResult(
            entities=[
                self._entity(
                    "ting",
                    "Ting",
                    f"ting:{_slug(self._cluster or 'unknown')}",
                    status="healthy" if active else "idle",
                    endpoints={"internal": self._base_url},
                    metadata={
                        "runsByStatus": counts,
                        "activeRuns": active,
                        "totalRuns": sum(counts.values()),
                    },
                )
            ]
        )


class MimirDiscoveryAdapter(_HttpServiceDiscoveryAdapter):
    """Discover a Mímir and the mounts it serves."""

    warning_name = "mimir"

    async def collect(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> DiscoveryResult:
        stats = await self._json(client, headers, "/api/v1/mimir/stats")
        mounts = await self._json(client, headers, "/api/v1/mimir/mounts")
        stats = stats if isinstance(stats, dict) else {}
        mounts = mounts if isinstance(mounts, list) else []

        node_id = f"mimir:{_slug(self._cluster or 'unknown')}"
        return DiscoveryResult(
            entities=[
                self._entity(
                    "mimir",
                    "Mímir",
                    node_id,
                    status="healthy" if stats.get("healthy") else "failed",
                    endpoints={"internal": self._base_url},
                    metadata={
                        "pages": int(stats.get("page_count") or 0),
                        "categories": list(stats.get("categories") or []),
                        "mountCount": len(mounts),
                        "mounts": [
                            {
                                "name": str(mount.get("name") or ""),
                                "role": str(mount.get("role") or ""),
                                "status": str(mount.get("status") or ""),
                                "pages": int(mount.get("pages") or 0),
                                "sizeKb": int(mount.get("size_kb") or 0),
                            }
                            for mount in mounts
                            if isinstance(mount, dict)
                        ],
                    },
                )
            ]
        )


#: Hosts whose names mean "served from our own hardware".
_INTERNAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_INTERNAL_HOST_SUFFIXES = (".svc.cluster.local", ".internal", ".local")


#: Provider keys and vendors that denote weights running on our own hardware.
_SELF_HOSTED_PROVIDERS = frozenset({"local", "self-hosted", "vllm", "ollama"})


def _model_location(base_url: str, provider: Mapping[str, Any] | None = None) -> str:
    """Where a model is served from: our hardware, or someone else's.

    Prefers the provider's endpoint host. Matched against the parsed host,
    never against the whole URL — a substring test would read
    `https://localhost.example.com/` and
    `https://api.vendor.test/?v=.svc.cluster.local` as internal, and this value
    is what tells an operator whether their traffic leaves the building.

    Bifröst's catalogue API does not expose provider base URLs (every provider
    returns ""), so when there is no host the provider's own identity is the
    available signal: its key or vendor naming a self-hosted runtime. That is
    still read from real configuration, not guessed from a name pattern.
    """
    host = (urlsplit(base_url).hostname or "").lower() if base_url else ""
    if host:
        if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_HOST_SUFFIXES):
            return "internal"
        return "external"

    identity = {
        str((provider or {}).get("key") or "").lower(),
        str((provider or {}).get("vendor") or "").lower(),
    }
    if identity & _SELF_HOSTED_PROVIDERS:
        return "internal"
    if identity - {""}:
        return "external"
    return "unknown"


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
        realm: str = "",
        label_selector: str = "niuu.world/cluster",
        include_kinds: list[str] | None = None,
        timeout_seconds: float = 10.0,
        service_account_root: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cluster = cluster
        self._namespace = namespace
        # Nothing labels a cluster with its realm, so it comes from config.
        self._realm = realm
        self._label_selector = label_selector
        self._include_kinds = include_kinds or [
            "nodes",
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
                    # Nodes are cluster infrastructure, not niuu workloads, so
                    # they carry none of our labels. Filtering them by the
                    # workload selector would return nothing at all.
                    selector = None if kind == "nodes" else self._label_selector
                    response = await client.get(
                        f"{base_url}{path}",
                        headers=headers,
                        params={"labelSelector": selector} if selector else None,
                    )
                    if response.status_code == 403:
                        events.append(_adapter_warning("kubernetes", f"Forbidden listing {kind}"))
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    for item in payload.get("items", []) if isinstance(payload, dict) else []:
                        if isinstance(item, dict):
                            resource_kind = _singular_resource_kind(kind)
                            if resource_kind == "node":
                                node = self._node_entity(item)
                                if node is not None:
                                    entities.append(node)
                                continue
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
        if kind == "nodes":
            return "/api/v1/nodes"
        return ""

    def _node_entity(self, item: dict[str, Any]) -> DiscoveredEntity | None:
        """Turn a Kubernetes Node into a host.

        Hosts are what make the graph show where things actually run — which
        box, with which GPU — rather than an undifferentiated cluster blob.
        """
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = str(metadata.get("name") or "").strip()
        if not name:
            return None

        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        capacity = status.get("capacity") if isinstance(status.get("capacity"), dict) else {}
        node_info = status.get("nodeInfo") if isinstance(status.get("nodeInfo"), dict) else {}
        labels = _clean_map(metadata.get("labels"))
        # Read roles from the raw labels: `node-role.kubernetes.io/control-plane`
        # conventionally has an empty value, which `_clean_map` drops.
        raw_labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}

        node_metadata: dict[str, Any] = {
            "os": str(node_info.get("osImage") or ""),
            "hw": str(node_info.get("architecture") or ""),
            "kernel": str(node_info.get("kernelVersion") or ""),
            "kubelet": str(node_info.get("kubeletVersion") or ""),
            "cores": _int_quantity(capacity.get("cpu")),
            "ram": _memory_gib(capacity.get("memory")),
            "roles": _node_roles(raw_labels),
        }
        gpu_count = _int_quantity(capacity.get("nvidia.com/gpu"))
        if gpu_count:
            node_metadata["gpu"] = labels.get("nvidia.com/gpu.product") or "nvidia"
            node_metadata["gpuCount"] = gpu_count

        return DiscoveredEntity(
            id=f"host:{_slug(self._cluster or 'unknown')}:{_slug(name)}",
            kind="host",
            name=name,
            realm=self._realm,
            cluster=self._cluster or "unknown",
            # Deliberately no `host=`: a host is placed by its cluster, and
            # naming itself would make it its own parent.
            status="healthy" if _condition_status(item, "Ready") else "failed",
            labels=labels,
            source_adapter=self.__class__.__name__,
            source_kind="kubernetes:node",
            source_uid=str(metadata.get("uid") or ""),
            metadata=node_metadata,
        )

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
        declared_component = labels.get("niuu.world/kind") or labels.get(
            "observatory.niuu.world/type"
        )
        component = declared_component or labels.get("app.kubernetes.io/component")
        app_name = labels.get("app.kubernetes.io/name")
        type_id = _type_id_for_component(
            component or "",
            app_name or "",
            declared=bool(declared_component),
        )
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
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        return DiscoveredEntity(
            id=entity_id,
            # No downgrade here. Whether a kind is renderable is the registry's
            # call, made once in `topology_from_discovery`; flattening it at the
            # adapter would hide an operator-registered type from the code that
            # knows about it.
            kind=type_id,
            name=display_name,
            realm=self._realm,
            cluster=cluster,
            namespace=namespace,
            # Only a pod knows which box it landed on. Namespace still decides
            # containment; this is what lets the graph relate a workload to
            # the host underneath it.
            host=str(spec.get("nodeName") or "") if resource_kind == "pod" else "",
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


#: Label domain Kubernetes uses to mark a node's roles.
_NODE_ROLE_DOMAIN = "node-role.kubernetes.io"


def _node_roles(labels: Mapping[str, Any]) -> list[str]:
    """Role names from `node-role.kubernetes.io/<role>` label keys.

    Splits on the separator and compares the domain exactly, so a key that
    merely starts with or contains the domain cannot contribute a role.
    """
    roles = [
        role
        for key in labels
        for domain, _, role in [str(key).partition("/")]
        if domain == _NODE_ROLE_DOMAIN and role
    ]
    return sorted(roles)


def _int_quantity(value: Any) -> int:
    """Parse a Kubernetes count quantity, tolerating milli-CPU suffixes."""
    raw = str(value or "").strip()
    if not raw:
        return 0
    if raw.endswith("m"):
        # 3500m CPU is 3 whole cores for display purposes.
        try:
            return int(float(raw[:-1]) / 1000)
        except ValueError:
            return 0
    try:
        return int(float(raw))
    except ValueError:
        return 0


_MEMORY_UNITS = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


def _memory_gib(value: Any) -> int:
    """Convert a Kubernetes memory quantity to whole GiB.

    Node capacity arrives as `263849876Ki`, which is unreadable in a tooltip.
    """
    raw = str(value or "").strip()
    if not raw:
        return 0
    for suffix, multiplier in _MEMORY_UNITS.items():
        if raw.endswith(suffix):
            try:
                return int(float(raw[: -len(suffix)]) * multiplier / 1024**3)
            except ValueError:
                return 0
    try:
        return int(float(raw) / 1024**3)
    except ValueError:
        return 0


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


def _type_id_for_component(component: str, app_name: str = "", *, declared: bool = False) -> str:
    """Best guess at an entity type for a Kubernetes workload.

    `declared` means the component came from a deliberate niuu/observatory
    label rather than a generic `app.kubernetes.io/component`. A declaration is
    taken verbatim so an operator can register a new type and label workloads
    with it, without a code change; a generic component is only a hint, so it
    stays a lookup and falls back to `service` rather than inventing a type
    from whatever a third-party chart happened to write.
    """
    normalized = _slug(component)
    if declared and normalized:
        return normalized
    if normalized in _SEED_TYPE_IDS:
        return normalized
    mapped = _COMPONENT_TYPES.get(component) or _COMPONENT_TYPES.get(normalized)
    if mapped:
        return mapped
    return _COMPONENT_TYPES.get(app_name, "service")


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
