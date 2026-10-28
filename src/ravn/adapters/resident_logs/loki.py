"""Read resident logs from Loki, where every cluster's pod logs are collected.

Each cluster's collector ships its pod logs to one Loki under a per-cluster
tenant, and the Loki proxy exposes each tenant's read API under a path named
for the cluster (``/<cluster>/loki/api/v1/query_range``). A resident's
placement names its cluster, namespace and Deployment, which is everything a
stream selector needs.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from ravn.ports.resident_discovery import ResidentPlacement
from ravn.ports.resident_logs import ResidentLogSourceError

#: Kubernetes object names (RFC 1123 labels). Anything else could escape the
#: LogQL selector, so it is refused rather than quoted.
_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

#: Severity order shared with the managed-resident log pages.
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
_LEVEL_ALIASES = {"warn": "warning", "fatal": "critical", "trace": "debug"}
_LEVEL_WORD = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b")
_NANOSECONDS_PER_MILLISECOND = 1_000_000
_NANOSECONDS_PER_SECOND = 1_000_000_000


class LokiResidentLogAdapter:
    """Implements :class:`~ravn.ports.resident_logs.ResidentLogPort` over Loki."""

    def __init__(
        self,
        base_url: str,
        query_path: str = "/{cluster}/loki/api/v1/query_range",
        lookback_seconds: int = 86400,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError(
                "resident_logs: the Loki adapter needs base_url (the Loki or Loki-proxy URL)"
            )
        if "{cluster}" not in query_path:
            raise ValueError(
                f"resident_logs: query_path {query_path!r} must contain {{cluster}}, "
                "the placeholder for the resident's cluster"
            )
        self._base_url = base_url.rstrip("/")
        self._query_path = query_path
        self._lookback_seconds = lookback_seconds
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def read_logs(
        self,
        placement: ResidentPlacement,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> dict[str, Any]:
        url = self._base_url + self._query_path.format(cluster=_name(placement.cluster, "cluster"))
        now_ns = time.time_ns()
        params = {
            "query": _selector(placement, sources, min_level),
            "limit": str(lines),
            "direction": "backward",
            "start": str(now_ns - self._lookback_seconds * _NANOSECONDS_PER_SECOND),
            "end": str(now_ns),
        }
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ResidentLogSourceError(
                f"Loki at {url} could not return the logs of {placement.namespace}/"
                f"{placement.workload}: {exc}"
            ) from exc
        streams = (payload.get("data") or {}).get("result") if isinstance(payload, dict) else None
        if not isinstance(streams, list) or not all(isinstance(item, dict) for item in streams):
            raise ResidentLogSourceError(
                f"Loki at {url} answered without a stream result: {str(payload)[:200]}"
            )
        entries = [entry for stream in streams for entry in _stream_entries(stream)]
        entries.sort(key=lambda entry: entry["timestampMs"])
        entries = entries[-lines:]
        return {"entries": entries, "bufferTotal": len(entries)}


def _name(value: str, what: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError(f"resident {what} {value!r} is not a Kubernetes name")
    return value


def _selector(placement: ResidentPlacement, sources: tuple[str, ...], min_level: str) -> str:
    namespace = _name(placement.namespace, "namespace")
    workload = _name(placement.workload, "workload")
    matchers = [
        f'k8s_namespace_name="{namespace}"',
        # A Deployment's pods are <workload>-<replicaset hash>-<pod hash>; the
        # exact shape keeps a longer workload name with this prefix out.
        f'k8s_pod_name=~"{workload}-[a-z0-9]+-[a-z0-9]+"',
    ]
    if sources:
        containers = "|".join(_name(source, "container") for source in sources)
        matchers.append(f'k8s_container_name=~"{containers}"')
    selector = "{" + ", ".join(matchers) + "}"
    minimum = _level_rank(min_level)
    if minimum is None:
        return selector
    levels = [name for name, rank in _LEVELS.items() if rank >= minimum]
    words = levels + [alias for alias, name in _LEVEL_ALIASES.items() if name in levels]
    return selector + f' | detected_level=~"(?i){"|".join(words)}"'


def _level_rank(min_level: str) -> int | None:
    if not min_level:
        return None
    word = min_level.lower()
    level = _LEVEL_ALIASES.get(word, word)
    if level not in _LEVELS:
        allowed = ", ".join(_LEVELS)
        raise ValueError(f"min_level {min_level!r} must be one of: {allowed}")
    return _LEVELS[level]


def _stream_entries(stream: dict[str, Any]) -> list[dict[str, Any]]:
    labels = stream.get("stream") or {}
    container = str(labels.get("k8s_container_name") or "")
    pod = str(labels.get("k8s_pod_name") or "")
    detected = str(labels.get("detected_level") or "").lower()
    entries = []
    for value in stream.get("values") or []:
        timestamp_ns, line = str(value[0]), str(value[1])
        entries.append(
            {
                "timestampMs": int(timestamp_ns) // _NANOSECONDS_PER_MILLISECOND,
                "level": _entry_level(line, detected),
                "source": container,
                "target": container,
                "message": line,
                "fields": {"pod": pod},
            }
        )
    return entries


def _entry_level(line: str, detected: str) -> str:
    """The line's own level word, else Loki's detected level, else info."""
    match = _LEVEL_WORD.search(line)
    word = match.group(1).lower() if match else detected
    level = _LEVEL_ALIASES.get(word, word)
    return level if level in _LEVELS else "info"
