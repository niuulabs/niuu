"""Local platform discovery for the Observatory topology and event streams."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

JsonFetcher = Callable[[str], Awaitable[Any]]
DEFAULT_DISCOVERY_HOST = "127.0.0.1"
DEFAULT_DISCOVERY_PORT = 8080


@dataclass(frozen=True)
class DiscoverySnapshot:
    """Materialized observatory snapshot."""

    topology: dict[str, Any]
    events: list[dict[str, str]]


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hhmmss(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%H:%M:%S")


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "local"


def _node_status(healthy: bool | None, *, degraded: bool = False) -> str:
    if healthy is None:
        return "unknown"
    if not healthy:
        return "failed"
    if degraded:
        return "degraded"
    return "healthy"


def _safe_int(raw: object, default: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _cluster_identity() -> tuple[str, str]:
    import os

    namespace = os.environ.get("POD_NAMESPACE") or os.environ.get("NAMESPACE") or "local"
    cluster = (
        os.environ.get("OBSERVATORY_CLUSTER")
        or os.environ.get("NIUU_CLUSTER")
        or os.environ.get("HOSTNAME")
        or "platform"
    )
    return namespace, cluster


def _discovery_base_url(
    host: str = DEFAULT_DISCOVERY_HOST,
    port: int | str = DEFAULT_DISCOVERY_PORT,
) -> str:
    normalized_host = host.strip() or DEFAULT_DISCOVERY_HOST
    if normalized_host in {"0.0.0.0", "::", "[::]"}:
        normalized_host = DEFAULT_DISCOVERY_HOST
    normalized_port = str(port).strip() or str(DEFAULT_DISCOVERY_PORT)
    return f"http://{normalized_host}:{normalized_port}"


class ObservatoryDiscoveryService:
    """Discovers local NIUU platform state from sibling APIs."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        ttl_seconds: float = 10.0,
        timeout_seconds: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._base_url = (base_url or _discovery_base_url()).rstrip("/")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._timeout = timeout_seconds
        self._transport = transport
        self._fetch_json = fetch_json
        self._lock = asyncio.Lock()
        self._cached_at: datetime | None = None
        self._cached_snapshot: DiscoverySnapshot | None = None

    @property
    def base_url(self) -> str:
        """Return the base URL used for local platform discovery."""
        return self._base_url

    async def get_topology_snapshot(self) -> dict[str, Any]:
        return deepcopy((await self._get_snapshot()).topology)

    async def get_events(self) -> list[dict[str, str]]:
        return deepcopy((await self._get_snapshot()).events)

    async def _get_snapshot(self) -> DiscoverySnapshot:
        now = _utc_now()
        if (
            self._cached_snapshot is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl
        ):
            return self._cached_snapshot

        async with self._lock:
            now = _utc_now()
            if (
                self._cached_snapshot is not None
                and self._cached_at is not None
                and now - self._cached_at < self._ttl
            ):
                return self._cached_snapshot

            snapshot = await self._discover(now)
            self._cached_snapshot = snapshot
            self._cached_at = now
            return snapshot

    async def _discover(self, now: datetime) -> DiscoverySnapshot:
        if self._fetch_json is not None:
            fetch = self._fetch_json
            root_health = await self._safe_fetch(fetch, "/health")
            forge_resources = await self._safe_fetch(fetch, "/api/v1/forge/resources")
            ravn_status = await self._safe_fetch(fetch, "/api/v1/ravn/status")
            ravn_wardens = await self._safe_fetch(fetch, "/api/v1/ravn/wardens")
            ting_health = await self._safe_fetch(fetch, "/api/v1/ting/health")
            ting_summary = await self._safe_fetch(fetch, "/api/v1/ting/runs/summary")
            ting_active = await self._safe_fetch(fetch, "/api/v1/ting/runs/active")
            mimir_stats = await self._safe_fetch(fetch, "/api/v1/mimir/stats")
            mimir_mounts = await self._safe_fetch(fetch, "/api/v1/mimir/mounts")
            bifrost_providers = await self._safe_fetch(fetch, "/api/v1/bifrost/providers/health")
            bifrost_models = await self._safe_fetch(fetch, "/api/v1/bifrost/models")
        else:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                async def client_fetch(path: str) -> Any:
                    response = await client.get(path)
                    response.raise_for_status()
                    return response.json()

                (
                    root_health,
                    forge_resources,
                    ravn_status,
                    ravn_wardens,
                    ting_health,
                    ting_summary,
                    ting_active,
                    mimir_stats,
                    mimir_mounts,
                    bifrost_providers,
                    bifrost_models,
                ) = await asyncio.gather(
                    self._safe_fetch(client_fetch, "/health"),
                    self._safe_fetch(client_fetch, "/api/v1/forge/resources"),
                    self._safe_fetch(client_fetch, "/api/v1/ravn/status"),
                    self._safe_fetch(client_fetch, "/api/v1/ravn/wardens"),
                    self._safe_fetch(client_fetch, "/api/v1/ting/health"),
                    self._safe_fetch(client_fetch, "/api/v1/ting/runs/summary"),
                    self._safe_fetch(client_fetch, "/api/v1/ting/runs/active"),
                    self._safe_fetch(client_fetch, "/api/v1/mimir/stats"),
                    self._safe_fetch(client_fetch, "/api/v1/mimir/mounts"),
                    self._safe_fetch(client_fetch, "/api/v1/bifrost/providers/health"),
                    self._safe_fetch(client_fetch, "/api/v1/bifrost/models"),
                )

        namespace, cluster_label = _cluster_identity()
        realm_id = f"realm:{_slug(namespace)}"
        cluster_id = f"cluster:{_slug(cluster_label)}"
        timestamp = _iso(now)

        nodes: list[dict[str, Any]] = [
            {
                "id": realm_id,
                "typeId": "realm",
                "label": namespace,
                "parentId": None,
                "status": "healthy",
                "dns": namespace,
                "purpose": "discovered local platform surface",
            },
            {
                "id": cluster_id,
                "typeId": "cluster",
                "label": cluster_label,
                "parentId": realm_id,
                "status": "healthy",
                "purpose": "mounted NIUU deployment",
                "zone": namespace,
            },
            {
                "id": "service:observatory",
                "typeId": "service",
                "label": "Observatory",
                "parentId": cluster_id,
                "status": "healthy",
                "svcType": "observatory",
            },
            {
                "id": "service:niuu",
                "typeId": "service",
                "label": "Niuu",
                "parentId": cluster_id,
                "status": _node_status(bool(root_health), degraded=False),
                "svcType": "niuu",
            },
        ]
        edges: list[dict[str, str]] = [
            {
                "id": "edge:realm-cluster",
                "sourceId": realm_id,
                "targetId": cluster_id,
                "kind": "solid",
            }
        ]
        events: list[dict[str, str]] = []

        if isinstance(forge_resources, dict):
            host_ids: list[str] = []
            for node in forge_resources.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                host_id = f"host:{_slug(str(node.get('name') or 'node'))}"
                host_ids.append(host_id)
                labels = node.get("labels") if isinstance(node.get("labels"), dict) else {}
                nodes.append(
                    {
                        "id": host_id,
                        "typeId": "host",
                        "label": str(node.get("name") or host_id),
                        "parentId": realm_id,
                        "status": "healthy",
                        "cluster": cluster_id,
                        "os": str(labels.get("kubernetes.io/os") or "unknown"),
                        "gpu": str(
                            labels.get("nvidia.com/gpu.product")
                            or labels.get("beta.kubernetes.io/instance-type")
                            or ""
                        )
                        or None,
                    }
                )
                edges.append(
                    {
                        "id": f"edge:{cluster_id}:{host_id}",
                        "sourceId": cluster_id,
                        "targetId": host_id,
                        "kind": "dashed-long",
                    }
                )
            nodes.append(
                {
                    "id": "service:volundr",
                    "typeId": "volundr",
                    "label": "Völundr",
                    "parentId": cluster_id,
                    "status": "healthy",
                    "activeSessions": 0,
                    "maxSessions": len(host_ids),
                }
            )
            edges.append(
                {
                    "id": "edge:observatory-volundr",
                    "sourceId": "service:observatory",
                    "targetId": "service:volundr",
                    "kind": "soft",
                }
            )

        if isinstance(ravn_status, dict) or isinstance(ravn_wardens, list):
            healthy = None
            if isinstance(ravn_status, dict) and isinstance(ravn_status.get("healthy"), bool):
                healthy = ravn_status["healthy"]
            nodes.append(
                {
                    "id": "service:ravn",
                    "typeId": "service",
                    "label": "Ravn",
                    "parentId": cluster_id,
                    "status": _node_status(
                        healthy if healthy is not None else ravn_wardens is not None
                    ),
                    "svcType": "ravn",
                }
            )
            edges.append(
                {
                    "id": "edge:observatory-ravn",
                    "sourceId": "service:observatory",
                    "targetId": "service:ravn",
                    "kind": "soft",
                }
            )
            if isinstance(ravn_wardens, list):
                for raw_warden in ravn_wardens:
                    if not isinstance(raw_warden, dict):
                        continue
                    warden_id = str(raw_warden.get("id") or raw_warden.get("name") or "warden")
                    node_id = f"ravn:{_slug(warden_id)}"
                    nodes.append(
                        {
                            "id": node_id,
                            "typeId": "ravn_long",
                            "label": str(raw_warden.get("name") or warden_id),
                            "parentId": cluster_id,
                            "status": "idle",
                            "persona": str(raw_warden.get("persona") or "unknown"),
                            "specialty": str(raw_warden.get("deployment") or "warden"),
                            "tokens": 0,
                        }
                    )
                    edges.append(
                        {
                            "id": f"edge:service:ravn:{node_id}",
                            "sourceId": "service:ravn",
                            "targetId": node_id,
                            "kind": "solid",
                        }
                    )

        if isinstance(ting_summary, dict) or isinstance(ting_health, dict):
            active_sagas = 0
            pending_runs = 0
            if isinstance(ting_summary, dict):
                active_sagas = sum(
                    _safe_int(count)
                    for status_name, count in ting_summary.items()
                    if status_name not in {"done", "completed", "closed"}
                )
                pending_runs = _safe_int(ting_summary.get("queued")) + _safe_int(
                    ting_summary.get("pending")
                )
            nodes.append(
                {
                    "id": "service:ting",
                    "typeId": "ting",
                    "label": "Ting",
                    "parentId": cluster_id,
                    "status": _node_status((ting_summary is not None) or (ting_health is not None)),
                    "mode": "active",
                    "activeSagas": active_sagas,
                    "pendingRuns": pending_runs,
                }
            )
            edges.append(
                {
                    "id": "edge:observatory-ting",
                    "sourceId": "service:observatory",
                    "targetId": "service:ting",
                    "kind": "soft",
                }
            )
            if isinstance(ting_active, list):
                for run in ting_active:
                    if not isinstance(run, dict):
                        continue
                    run_id = str(run.get("tracker_id") or run.get("identifier") or "run")
                    node_id = f"run:{_slug(run_id)}"
                    nodes.append(
                        {
                            "id": node_id,
                            "typeId": "run",
                            "label": str(run.get("title") or run.get("identifier") or run_id),
                            "parentId": cluster_id,
                            "status": "observing",
                            "state": str(run.get("status") or "unknown"),
                            "confidence": float(run.get("confidence") or 0.0),
                        }
                    )
                    edges.append(
                        {
                            "id": f"edge:ting:{node_id}",
                            "sourceId": "service:ting",
                            "targetId": node_id,
                            "kind": "run",
                        }
                    )

        if isinstance(mimir_stats, dict) or isinstance(mimir_mounts, list):
            page_count = _safe_int(
                mimir_stats.get("page_count") if isinstance(mimir_stats, dict) else 0
            )
            healthy = None
            if isinstance(mimir_stats, dict) and isinstance(mimir_stats.get("healthy"), bool):
                healthy = mimir_stats["healthy"]
            nodes.append(
                {
                    "id": "service:mimir",
                    "typeId": "mimir",
                    "label": "Mímir",
                    "parentId": cluster_id,
                    "status": _node_status(
                        healthy if healthy is not None else mimir_stats is not None
                    ),
                    "pages": page_count,
                    "writes": 0,
                    "mountCount": len(mimir_mounts) if isinstance(mimir_mounts, list) else 0,
                    "mounts": [
                        str(mount.get("name") or "mount")
                        for mount in mimir_mounts
                        if isinstance(mount, dict)
                    ][:8]
                    if isinstance(mimir_mounts, list)
                    else [],
                }
            )
            edges.append(
                {
                    "id": "edge:observatory-mimir",
                    "sourceId": "service:observatory",
                    "targetId": "service:mimir",
                    "kind": "soft",
                }
            )

        if isinstance(bifrost_providers, list) or isinstance(bifrost_models, list):
            providers: list[str] = []
            degraded = False
            if isinstance(bifrost_providers, list):
                for provider in bifrost_providers:
                    if not isinstance(provider, dict):
                        continue
                    providers.append(
                        str(provider.get("key") or provider.get("vendor") or "provider")
                    )
                    state = str(provider.get("state") or "").lower()
                    if state and state not in {"healthy", "ready", "ok", "online"}:
                        degraded = True
            nodes.append(
                {
                    "id": "service:bifrost",
                    "typeId": "bifrost",
                    "label": "Bifröst",
                    "parentId": cluster_id,
                    "status": _node_status(
                        (bifrost_models is not None) or (bifrost_providers is not None),
                        degraded=degraded,
                    ),
                    "providers": providers,
                    "reqPerMin": 0,
                    "cacheHitRate": 0,
                }
            )
            edges.append(
                {
                    "id": "edge:observatory-bifrost",
                    "sourceId": "service:observatory",
                    "targetId": "service:bifrost",
                    "kind": "soft",
                }
            )
            if isinstance(bifrost_models, list):
                for model in bifrost_models[:12]:
                    if not isinstance(model, dict):
                        continue
                    model_id = str(model.get("id") or model.get("name") or "model")
                    node_id = f"model:{_slug(model_id)}"
                    nodes.append(
                        {
                            "id": node_id,
                            "typeId": "model",
                            "label": str(model.get("name") or model_id),
                            "parentId": "service:bifrost",
                            "status": "healthy" if model.get("enabled", True) else "idle",
                            "provider": str(model.get("provider") or "unknown"),
                            "location": str(model.get("vendor") or ""),
                            "model": model_id,
                        }
                    )
                    edges.append(
                        {
                            "id": f"edge:bifrost:{node_id}",
                            "sourceId": "service:bifrost",
                            "targetId": node_id,
                            "kind": "solid",
                        }
                    )

        events.extend(
            self._build_events(
                now=now,
                ravn_wardens=ravn_wardens,
                ting_summary=ting_summary,
                ting_active=ting_active,
                mimir_stats=mimir_stats,
                mimir_mounts=mimir_mounts,
                bifrost_providers=bifrost_providers,
            )
        )

        topology = {"nodes": nodes, "edges": edges, "timestamp": timestamp}
        return DiscoverySnapshot(topology=topology, events=events)

    async def _safe_fetch(self, fetcher: JsonFetcher, path: str) -> Any:
        try:
            return await fetcher(path)
        except Exception:
            return None

    def _build_events(
        self,
        *,
        now: datetime,
        ravn_wardens: Any,
        ting_summary: Any,
        ting_active: Any,
        mimir_stats: Any,
        mimir_mounts: Any,
        bifrost_providers: Any,
    ) -> list[dict[str, str]]:
        stamp = _hhmmss(now)
        events: list[dict[str, str]] = []
        if isinstance(ting_active, list) and ting_active:
            first = ting_active[0] if isinstance(ting_active[0], dict) else {}
            active_run_key = str(
                first.get("tracker_id") or first.get("identifier") or "active"
            )
            events.append(
                {
                    "id": f"run:{_slug(active_run_key)}",
                    "time": stamp,
                    "type": "RUN",
                    "subject": str(
                        first.get("identifier") or first.get("tracker_id") or "active run"
                    ),
                    "body": str(first.get("status") or "active"),
                }
            )
        elif isinstance(ting_summary, dict):
            events.append(
                {
                    "id": "run:summary",
                    "time": stamp,
                    "type": "TING",
                    "subject": "run summary",
                    "body": ", ".join(
                        f"{name}={_safe_int(count)}" for name, count in sorted(ting_summary.items())
                    )
                    or "no visible runs",
                }
            )
        if isinstance(ravn_wardens, list):
            events.append(
                {
                    "id": "ravn:wardens",
                    "time": stamp,
                    "type": "RAVN",
                    "subject": "wardens",
                    "body": f"{len(ravn_wardens)} persisted wardens discovered",
                }
            )
        if isinstance(mimir_stats, dict):
            mount_count = len(mimir_mounts) if isinstance(mimir_mounts, list) else 0
            events.append(
                {
                    "id": "mimir:stats",
                    "time": stamp,
                    "type": "MIMIR",
                    "subject": "memory surface",
                    "body": (
                        f"{_safe_int(mimir_stats.get('page_count'))} "
                        f"pages across {mount_count} mounts"
                    ),
                }
            )
        if isinstance(bifrost_providers, list):
            degraded = [
                str(provider.get("key") or provider.get("vendor") or "provider")
                for provider in bifrost_providers
                if isinstance(provider, dict)
                and str(provider.get("state") or "").lower()
                not in {"", "healthy", "ready", "ok", "online"}
            ]
            events.append(
                {
                    "id": "bifrost:providers",
                    "time": stamp,
                    "type": "BIFROST",
                    "subject": "provider health",
                    "body": (
                        f"{len(bifrost_providers)} providers visible"
                        if not degraded
                        else f"degraded providers: {', '.join(degraded)}"
                    ),
                }
            )
        return events
