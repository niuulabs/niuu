"""Residents the Forge discovers instead of deploying: Ravns deployed from infrastructure.

A Valkyrie runs as a plain Deployment in its own cluster, deployed straight from
infrastructure, so the Forge never had a record of it and could not open its
sessions ("channels"). Each one runs the same Ravn OpenClaw shim a deployed Ravn
resident runs, reachable over its own ingress. This backend lists those residents
for the Forge (``ResidentDiscoverySource``), routes the Forge's Ravn session
controller to each shim (``ResidentOpenClawShimHost``) and observes whether the shim
answers. The Forge keeps a record per resident and drives its sessions, chat and
notifications with the controllers it already has; it never deploys, restarts,
suspends or removes a discovered resident, which is done where it is deployed.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: volundr.adapters.outbound.discovered_resident_runtime.
             DiscoveredResidentRuntimeController
    residents:
      - key: valkyrie-glitnir-k8s          # its id elsewhere (the Valkyrie catalog)
        name: Hildr
        owner_id: <user id>                # who the record belongs to
        tenant_id: <tenant id>
        shim_url: https://hildr.glitnir.example/openclaw
        chat_endpoint: wss://yggdrasil.example/rooms/hildr/session   # optional
        persona_name: k8s-valkyrie         # optional
        profile_id: discovered-ravn        # optional; a profile on the discovered backend
        placement: {cluster: glitnir, namespace: nats, workload: valkyrie-glitnir-k8s}
    shim_tokens: '{"valkyrie-glitnir-k8s": "..."}'   # via secret_kwargs_env, one per resident
    loki_base_url: https://loki.example   # logs by placement from each cluster's Loki tenant
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from niuu.adapters.loki_pod_logs import LokiPodLogError, LokiPodLogReader
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.models import (
    ResidentBackend,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentEndpoint,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import (
    DiscoveredResident,
    ResidentDiscoverySource,
    ResidentLogsUnavailableError,
    ResidentOpenClawShimHost,
    ResidentOpenClawTokens,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
)

DEFAULT_PROFILE_ID = "discovered-ravn"
_HTTPS_PORT = 443
_CONDITION = "ShimReachable"


@dataclass(frozen=True)
class _Entry:
    key: str
    name: str
    owner_id: str
    tenant_id: str
    profile_id: str
    persona_name: str
    model: str
    shim_url: str
    chat_endpoint: str
    placement: tuple[str, str, str] | None


class DiscoveredResidentRuntimeController(
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentOpenClawShimHost,
    ResidentDiscoverySource,
):
    """The ``discovered`` backend: configured residents running outside the Forge."""

    def __init__(
        self,
        residents: list[dict[str, Any]],
        shim_tokens: str | dict[str, str] = "",
        loki_base_url: str = "",
        health_timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._entries = _entries(residents)
        self._tokens = _tokens(shim_tokens, self._entries)
        self._client = httpx.AsyncClient(timeout=health_timeout_seconds, transport=transport)
        self._logs = (
            LokiPodLogReader(base_url=loki_base_url, transport=transport) if loki_base_url else None
        )

    @property
    def backend(self) -> ResidentBackend:
        return ResidentBackend.DISCOVERED

    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        return profile.backend is ResidentBackend.DISCOVERED

    async def discovered_residents(self) -> list[DiscoveredResident]:
        return [
            DiscoveredResident(
                key=entry.key,
                name=entry.name,
                owner_id=entry.owner_id,
                tenant_id=entry.tenant_id,
                profile_id=entry.profile_id,
                persona_name=entry.persona_name,
                model=entry.model,
            )
            for entry in self._entries.values()
        ]

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        raise RuntimeError(_not_managed(runtime, "deployed"))

    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        """Report the resident active while its shim answers its health check."""
        entry = self._entries.get(_key(runtime))
        if entry is None:
            return ResidentRuntimeObservation(
                observed_state=ResidentObservedState.FAILED,
                backend_ref=dict(runtime.backend_ref),
                conditions=[
                    _condition(
                        ResidentConditionStatus.FALSE,
                        "NotDiscovered",
                        f"No discovered resident has the key {_key(runtime)!r}; its record is "
                        "removed on the next discovery pass.",
                    )
                ],
            )
        health_url = entry.shim_url.rstrip("/") + "/health"
        try:
            response = await self._client.get(health_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            state = ResidentObservedState.FAILED
            condition = _condition(
                ResidentConditionStatus.FALSE,
                "ShimUnreachable",
                f"The resident's OpenClaw shim at {health_url} did not answer: {exc}",
            )
        else:
            state = ResidentObservedState.ACTIVE
            condition = _condition(ResidentConditionStatus.TRUE, "ShimHealthy", "")
        endpoints = (
            [ResidentEndpoint(kind="chat", protocol="skuld-v1", url=entry.chat_endpoint)]
            if entry.chat_endpoint
            else []
        )
        return ResidentRuntimeObservation(
            observed_state=state,
            backend_ref={"key": entry.key, "shim_url": entry.shim_url},
            endpoints=endpoints,
            conditions=[condition],
        )

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        raise RuntimeError(_not_managed(runtime, "restarted"))

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        raise RuntimeError(_not_managed(runtime, "suspended"))

    async def resume(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile | None = None,
    ) -> ResidentRuntimeObservation:
        raise RuntimeError(_not_managed(runtime, "resumed"))

    async def delete(self, runtime: ResidentRuntime) -> bool:
        raise RuntimeError(_not_managed(runtime, "removed"))

    def set_resident_openclaw_tokens(self, tokens: ResidentOpenClawTokens) -> None:
        """Unused: a discovered resident's token is the one it was deployed with,
        configured here, so the Forge mints and keeps none for it."""

    async def resident_openclaw_token(self, runtime: ResidentRuntime) -> str:
        key = _key(runtime)
        token = self._tokens.get(key)
        if token is None:
            raise RuntimeError(
                f"No OpenClaw shim token is configured for discovered resident {key!r}"
            )
        return token

    def resident_openclaw_target(
        self,
        runtime: ResidentRuntime,
        port: int,
    ) -> SessionProxyTarget | None:
        """The resident's shim at its configured URL; ``port`` is the in-cluster one, unused."""
        entry = self._entries.get(_key(runtime))
        if entry is None:
            return None
        parts = urlsplit(entry.shim_url)
        return SessionProxyTarget(
            service_url=entry.shim_url,
            connect_host=parts.hostname or "",
            connect_port=parts.port or _HTTPS_PORT,
            connect_secure=True,
        )

    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        """The resident's pod logs from its cluster's Loki tenant, found by its placement."""
        entry = self._entries.get(_key(runtime))
        if entry is None:
            raise ResidentLogsUnavailableError(f"Resident {runtime.name} is no longer discovered")
        if self._logs is None:
            raise ResidentLogsUnavailableError(
                "No log source is configured for discovered residents: set loki_base_url on "
                "the discovered resident backend"
            )
        if entry.placement is None:
            raise ResidentLogsUnavailableError(
                f"Discovered resident {entry.key!r} has no placement "
                "{cluster, namespace, workload}, so its logs cannot be located"
            )
        cluster, namespace, workload = entry.placement
        try:
            rows = await self._logs.read(
                cluster=cluster,
                namespace=namespace,
                workload=workload,
                lines=lines,
                sources=sources,
                min_level=min_level,
            )
        except LokiPodLogError as exc:
            raise RuntimeError(str(exc)) from exc
        return ResidentLogPage(
            entries=[ResidentLogEntry.model_validate(row) for row in rows],
            buffer_total=len(rows),
        )

    async def close(self) -> None:
        await self._client.aclose()
        if self._logs is not None:
            await self._logs.aclose()


def _key(runtime: ResidentRuntime) -> str:
    return str(runtime.backend_ref.get("key") or "")


def _not_managed(runtime: ResidentRuntime, verb: str) -> str:
    return (
        f"Resident {runtime.name} runs outside the Forge (discovered); it is {verb} where "
        "it is deployed, not here"
    )


def _condition(status: ResidentConditionStatus, reason: str, message: str) -> ResidentCondition:
    return ResidentCondition(type=_CONDITION, status=status, reason=reason, message=message)


def _entries(residents: list[dict[str, Any]]) -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}
    for index, raw in enumerate(residents):
        if not isinstance(raw, dict):
            raise ValueError(f"discovered resident #{index} must be an object")
        missing = [
            field
            for field in ("key", "name", "owner_id", "tenant_id", "shim_url")
            if not str(raw.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(f"discovered resident #{index} needs {', '.join(missing)}")
        key = str(raw["key"]).strip()
        if key in entries:
            raise ValueError(f"discovered resident key {key!r} is listed more than once")
        shim_url = str(raw["shim_url"]).strip()
        parts = urlsplit(shim_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError(
                f"discovered resident {key!r}: shim_url {shim_url!r} must be an https:// URL "
                "(the shim's route on the resident's ingress)"
            )
        entries[key] = _Entry(
            key=key,
            name=str(raw["name"]).strip(),
            owner_id=str(raw["owner_id"]).strip(),
            tenant_id=str(raw["tenant_id"]).strip(),
            profile_id=str(raw.get("profile_id") or DEFAULT_PROFILE_ID),
            persona_name=str(raw.get("persona_name") or ""),
            model=str(raw.get("model") or ""),
            shim_url=shim_url,
            chat_endpoint=str(raw.get("chat_endpoint") or ""),
            placement=_placement(key, raw.get("placement")),
        )
    return entries


def _placement(key: str, raw: Any) -> tuple[str, str, str] | None:
    if raw is None:
        return None
    fields = ("cluster", "namespace", "workload")
    if not isinstance(raw, dict) or not all(str(raw.get(name) or "").strip() for name in fields):
        raise ValueError(
            f"discovered resident {key!r}: placement needs cluster, namespace and workload"
        )
    return (str(raw["cluster"]), str(raw["namespace"]), str(raw["workload"]))


def _tokens(shim_tokens: str | dict[str, str], entries: dict[str, _Entry]) -> dict[str, str]:
    """Each resident's shim token; every resident needs one and every token a resident."""
    if isinstance(shim_tokens, str):
        raw: Any = json.loads(shim_tokens) if shim_tokens.strip() else {}
    else:
        raw = shim_tokens
    if not isinstance(raw, dict):
        raise ValueError("shim_tokens must be a JSON object of resident key to shim token")
    tokens = {str(key): str(value) for key, value in raw.items() if str(value).strip()}
    missing = sorted(set(entries) - set(tokens))
    if missing:
        raise ValueError(
            f"No OpenClaw shim token for discovered resident(s) {', '.join(missing)}: add each "
            "to shim_tokens (secret_kwargs_env)"
        )
    unknown = sorted(set(tokens) - set(entries))
    if unknown:
        raise ValueError(
            f"shim_tokens names resident(s) {', '.join(unknown)} that are not discovered"
        )
    return tokens
