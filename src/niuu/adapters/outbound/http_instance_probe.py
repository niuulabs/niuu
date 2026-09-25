"""HTTP-backed reachability probe for registered runtime instances."""

from __future__ import annotations

import httpx
from starlette.types import ASGIApp

from niuu.domain.models import RegisteredInstance
from niuu.ports.instance_probe import InstanceProbePort, InstanceProbeResult


def _uses_embedded_transport(instance: RegisteredInstance) -> bool:
    return str(instance.config.get("transport", "")).strip().lower() == "embedded"


class HttpInstanceProbeAdapter(InstanceProbePort):
    """Probes an instance's ``/health`` endpoint over HTTP, or the embedded ASGI app.

    Shared by register-time checks, the manual "test endpoint" action, and the
    periodic health loop, so all three agree on what "reachable" means.
    """

    def __init__(self, *, timeout_seconds: float, embedded_app: ASGIApp | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._embedded_app = embedded_app

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
            response = await client.get("/health")
        return self._result_from_response(instance, response)

    async def _probe_http(self, instance: RegisteredInstance) -> InstanceProbeResult:
        url = f"{instance.base_url}/health"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
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
