"""Observability endpoints for Bifröst.

Exposes three routes, all unauthenticated:

* ``GET /metrics``  — Prometheus text-format metrics.
* ``GET /healthz``  — Liveness probe (always 200 if the process is alive).
* ``GET /readyz``   — Readiness probe (200 when at least one provider is
                       reachable and the usage store is responsive).

``/metrics`` is deliberately exempt from auth, same as the two probes: it is
scraped by Prometheus on the app port with no credential (see
``charts/bifrost`` values, and Envoy's own bypass for it in the mesh's
authz policy), so gating it here would silently stop metrics collection the
moment any cluster's ``auth_mode`` leaves ``open`` — a regression nobody
would notice until dashboards went blank. Serving it on a separate,
network-restricted port is a real alternative but out of scope for this
change; exempting it here matches what Envoy already does in front of it.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from bifrost import metrics as _m
from bifrost.config import BifrostConfig
from bifrost.ports.usage_store import UsageStore
from bifrost.router import ModelRouter

logger = logging.getLogger(__name__)

_CONTENT_TYPE_OPENMETRICS = "text/plain; version=0.0.4; charset=utf-8"


def create_observability_router(
    config: BifrostConfig,
    router: ModelRouter,
    store: UsageStore,
) -> APIRouter:
    """Build and return an ``APIRouter`` with /metrics, /healthz, and /readyz.

    Args:
        config: Gateway configuration (used to discover provider base URLs).
        router: Model router (used for provider reachability checks).
        store:  Usage store (used for DB liveness checks in /readyz).

    Returns:
        A configured ``APIRouter``. None of its routes require a credential —
        see the module docstring for why ``/metrics`` is exempt too.
    """
    obs_router = APIRouter()
    _http_client = httpx.AsyncClient(timeout=5.0)
    # Expose the client so the app lifespan can close it on shutdown.
    obs_router.http_client = _http_client  # type: ignore[attr-defined]

    @obs_router.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        """Liveness probe — always returns 200 while the process is alive."""
        return "ok"

    @obs_router.get("/readyz")
    async def readyz() -> Response:
        """Readiness probe — 200 when DB and at least one provider are reachable.

        Checks:
        1. Usage store is responsive (simple query).
        2. At least one configured provider's base URL responds to an HTTP
           request within a short timeout.

        Returns:
            200 with ``{"status": "ready"}`` when all checks pass.
            503 with a JSON body listing failing checks otherwise.
        """
        failures: list[str] = []

        # ── Store check ───────────────────────────────────────────────────
        try:
            # A summarise call with no filters is cheap and exercises the
            # connection path without touching any real data.
            await store.summarise()
        except Exception as exc:
            logger.warning("readyz: usage store check failed: %s", exc)
            failures.append("usage_store: check failed")

        # ── Provider reachability check ───────────────────────────────────
        # We just need at least one provider base URL to accept a connection.
        # A HEAD request to the base URL is cheap and avoids consuming quota.
        provider_ok = False
        for provider_name, provider_cfg in config.providers.items():
            base_url = config.effective_base_url(provider_name)
            if not base_url:
                continue
            try:
                resp = await _http_client.head(base_url)
                # Any HTTP response (even 4xx) means the host is reachable.
                if resp.status_code < 600:  # noqa: PLR2004
                    provider_ok = True
                    break
            except Exception as exc:
                logger.debug(
                    "readyz: provider %s (%s) unreachable: %s", provider_name, base_url, exc
                )

        if not provider_ok and config.providers:
            failures.append("providers: no provider base URL is reachable")

        if failures:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "failures": failures},
            )

        return JSONResponse(content={"status": "ready"})

    @obs_router.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> PlainTextResponse:
        """Prometheus text-format metrics endpoint — unauthenticated, see module docstring.

        Exposes all Bifröst gateway metrics in the standard Prometheus
        text exposition format (version 0.0.4).

        Metrics exposed:
            bifrost_requests_total{provider,model,status}
            bifrost_request_duration_seconds{provider,model}
            bifrost_tokens_total{provider,model,type}
            bifrost_cost_usd_total{provider,model}
            bifrost_cache_hits_total{provider,model}
            bifrost_cache_misses_total{provider,model}
            bifrost_quota_rejections_total{agent_id}
            bifrost_rule_hits_total{rule_name,action}
        """
        text = _m.REGISTRY.generate_text()
        return PlainTextResponse(content=text, media_type=_CONTENT_TYPE_OPENMETRICS)

    return obs_router
