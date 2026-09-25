"""HTTP-backed reachability probe for registered runtime instances."""

from __future__ import annotations

import httpx
from starlette.types import ASGIApp

from niuu.adapters.outbound.guild_transport import (
    GuildTransportError,
    build_guild_httpx_client,
)
from niuu.domain.models import InstanceKind, RegisteredInstance
from niuu.ports.instance_probe import InstanceProbePort, InstanceProbeResult

#: Health path per instance kind, appended to ``instance.base_url``.
#:
#: Every default here is deliberately the path under that service's own API
#: prefix (``/api/v1/<service>/health``), not a bare ``/health``. Two real
#: deployment shapes break on a bare path:
#:
#: - A standalone service (e.g. an Observatory scout) whose ingress only
#:   routes its own ``/api/v1/<service>`` prefix — a bare ``/health`` never
#:   reaches the pod, so the probe reports a healthy instance as unreachable
#:   forever.
#: - A host shared with the web-next SPA, whose nginx also answers a bare
#:   ``/health`` itself (containers/niuu-web/nginx.conf) — the probe gets a
#:   200 from the frontend's static health check and reports "ok" regardless
#:   of whether the actual backend is up.
#:
#: An instance whose base_url does not follow the kind's usual convention
#: (e.g. it already ends in a version prefix) overrides this via
#: ``config.health_path`` — see ``_resolve_health_path``.
DEFAULT_HEALTH_PATHS: dict[InstanceKind, str] = {
    InstanceKind.VOLUNDR: "/api/v1/forge/health",
    InstanceKind.TING: "/api/v1/ting/health",
    InstanceKind.MIMIR: "/api/v1/mimir/health",
    InstanceKind.BIFROST: "/api/v1/bifrost/health",
    InstanceKind.RAVN: "/api/v1/ravn/health",
    InstanceKind.OBSERVATORY: "/api/v1/observatory/health",
    InstanceKind.GENERIC: "/health",
}

#: Used for a kind this build's DEFAULT_HEALTH_PATHS doesn't cover (e.g. a
#: newer InstanceKind added without updating the map here) — documented,
#: not a silent guess: the same conservative path GENERIC uses.
_FALLBACK_HEALTH_PATH = "/health"


def _uses_embedded_transport(instance: RegisteredInstance) -> bool:
    return str(instance.config.get("transport", "")).strip().lower() == "embedded"


class HttpInstanceProbeAdapter(InstanceProbePort):
    """Probes an instance's health endpoint over HTTP, or the embedded ASGI app.

    Shared by register-time checks, the manual "test endpoint" action, and the
    periodic health loop, so all three agree on what "reachable" means.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float,
        embedded_app: ASGIApp | None = None,
        health_paths: dict[str, str] | None = None,
    ) -> None:
        """``health_paths`` (kind value -> path) overrides DEFAULT_HEALTH_PATHS.

        Config only needs to carry the entries an operator wants to change —
        ``niuu.health.probe.health_paths`` (see src/niuu/config.py) merges
        on top of the built-in defaults rather than replacing them, so a
        fresh install with no overrides configured still probes every known
        kind correctly.
        """
        self._timeout_seconds = timeout_seconds
        self._embedded_app = embedded_app
        self._health_paths = {
            **{kind.value: path for kind, path in DEFAULT_HEALTH_PATHS.items()},
            **(health_paths or {}),
        }

    def _resolve_health_path(self, instance: RegisteredInstance) -> str:
        """Per-instance config.health_path wins; otherwise the kind's path."""
        override = instance.config.get("health_path")
        if isinstance(override, str) and override.strip():
            path = override.strip()
            return path if path.startswith("/") else f"/{path}"
        return self._health_paths.get(str(instance.kind), _FALLBACK_HEALTH_PATH)

    async def probe(self, instance: RegisteredInstance) -> InstanceProbeResult:
        try:
            if _uses_embedded_transport(instance):
                return await self._probe_embedded(instance)
            return await self._probe_http(instance)
        except Exception as exc:
            # The port guarantees probe() never raises. _probe_http already
            # turns the expected httpx failures into a result below; this is
            # the last-resort net for anything else — most notably an
            # in-process ASGI app bug surfacing through httpx.ASGITransport,
            # which is not an httpx.HTTPError and would otherwise propagate
            # and leave the instance's stored health stale (see
            # InstanceHealthChecker.check_instance, which has its own,
            # independent net for a misbehaving probe adapter).
            return InstanceProbeResult(
                ok=False, status_code=None, message=str(exc) or exc.__class__.__name__
            )

    async def _probe_embedded(self, instance: RegisteredInstance) -> InstanceProbeResult:
        if self._embedded_app is None:
            return InstanceProbeResult(
                ok=False,
                status_code=502,
                message="Embedded Forge target is not available in this process",
            )
        transport = httpx.ASGITransport(app=self._embedded_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://embedded.local",
        ) as client:
            response = await client.get(self._resolve_health_path(instance))
        return self._result_from_response(instance, response)

    async def _probe_http(self, instance: RegisteredInstance) -> InstanceProbeResult:
        url = f"{instance.base_url}{self._resolve_health_path(instance)}"
        try:
            client = await build_guild_httpx_client(
                instance,
                dial_url=instance.base_url,
                timeout_seconds=self._timeout_seconds,
                follow_redirects=True,
            )
            async with client:
                response = await client.get(url)
        except (httpx.HTTPError, GuildTransportError) as exc:
            # Several httpx transport errors stringify to "" (see
            # observatory_topology.py), which turns every unreachable
            # instance into a blank error. Fall back to the class name.
            return InstanceProbeResult(
                ok=False, status_code=None, message=str(exc) or exc.__class__.__name__
            )
        return self._result_from_response(instance, response)

    @staticmethod
    def _result_from_response(
        instance: RegisteredInstance, response: httpx.Response
    ) -> InstanceProbeResult:
        if response.status_code >= 400:
            return InstanceProbeResult(
                ok=False,
                status_code=response.status_code,
                message=f"Health probe failed for {instance.name} ({response.status_code})",
            )
        return InstanceProbeResult(
            ok=True,
            status_code=response.status_code,
            message=f"{instance.name} is reachable",
        )
