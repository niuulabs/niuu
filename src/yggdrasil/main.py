"""Standalone Yggdrasil realm health streaming service."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status

from niuu.service_runtime import run_service_app
from yggdrasil.api.routes_sse import create_sse_router
from yggdrasil.config import AdapterConfig, YggdrasilSettings
from yggdrasil.connection_manager import ConnectionManager
from yggdrasil.models import RealmAdapter
from yggdrasil.rate_limit import TokenBucketRateLimiter
from yggdrasil.sse.poller import RealmPoller


def _build_adapter(config: AdapterConfig) -> RealmAdapter:
    """Instantiate a configured realm adapter using the platform adapter convention."""
    module_path, class_name = config.adapter.rsplit(".", 1)
    adapter_class = getattr(importlib.import_module(module_path), class_name)
    adapter = adapter_class(**config.kwargs)
    if not isinstance(getattr(adapter, "realm_id", None), str):
        raise TypeError(f"configured adapter {config.adapter} must expose a string realm_id")
    if not callable(getattr(adapter, "get_summary", None)):
        raise TypeError(f"configured adapter {config.adapter} must define async get_summary()")
    return adapter


def create_app(
    *,
    settings: YggdrasilSettings | None = None,
    adapters: Sequence[RealmAdapter] | None = None,
) -> FastAPI:
    """Create the Yggdrasil service and compose its configured adapters."""
    resolved_settings = settings or YggdrasilSettings()
    resolved_adapters = (
        tuple(adapters)
        if adapters is not None
        else tuple(_build_adapter(config) for config in resolved_settings.adapters)
    )
    connection_manager = ConnectionManager(resolved_settings.max_sse_clients)
    poller = RealmPoller(resolved_adapters, connection_manager, resolved_settings.poll_interval)
    health_rate_limiter = TokenBucketRateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await poller.poll_once()
        poller.start()
        try:
            yield
        finally:
            await poller.stop()

    app = FastAPI(title="Yggdrasil", lifespan=lifespan)
    app.state.connection_manager = connection_manager
    app.state.health_rate_limiter = health_rate_limiter
    app.state.realm_poller = poller
    app.state.sse_router = create_sse_router(
        connection_manager,
        poller,
        resolved_settings.stream_disconnect_check_interval,
    )
    app.include_router(app.state.sse_router)

    @app.get("/health", tags=["Health"])
    async def health(request: Request) -> dict[str, str]:
        client_ip = request.client.host if request.client is not None else "unknown"
        retry_after = health_rate_limiter.retry_after(
            endpoint=request.url.path,
            client_ip=client_ip,
        )
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return {"status": "healthy"}

    return app


app = create_app()


def main() -> None:
    """Run the standalone Yggdrasil API service."""
    run_service_app("yggdrasil.main:app", default_port=8085)


if __name__ == "__main__":  # pragma: no cover
    main()
