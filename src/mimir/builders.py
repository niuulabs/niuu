"""Build the Mímir client adapters a runtime mounts, from configuration.

``build_mimir`` turns a ``MimirConfig`` into a ``MimirPort``: a single local
markdown store, or a ``CompositeMimirAdapter`` over several instances. It takes
any settings object carrying a ``mimir`` section, an ``environment.id`` and a
``gateway.platform`` section, so a host runtime (Ravn's ``Settings``) and the
standalone loader (``mimir.client_settings.MimirClientSettings``) share it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol

from mimir.client_config import MimirConfig

logger = logging.getLogger(__name__)

_WORKLOAD_EXCHANGE_URL_ENV = "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL"


class PlatformWorkloadSource(Protocol):
    """The ``gateway.platform`` fields the workload-auth fallbacks read."""

    enabled: bool
    base_url: str
    workload_token_file: str
    workload_exchange_url: str


class GatewaySection(Protocol):
    platform: PlatformWorkloadSource


class EnvironmentSection(Protocol):
    id: str


class MimirClientHost(Protocol):
    """Settings a Mímir client is built from."""

    mimir: MimirConfig
    environment: EnvironmentSection
    gateway: GatewaySection


def resolve_mimir_auth_token(auth_config: Any) -> str | None:
    """Resolve a Mimir bearer token from config without storing secrets in YAML."""
    if auth_config is None:
        return None
    if auth_config.token:
        return auth_config.token
    token_env = getattr(auth_config, "token_env", None)
    if token_env:
        token = os.environ.get(str(token_env), "").strip()
        if token:
            return token
    token_file = getattr(auth_config, "token_file", None)
    if token_file:
        try:
            token = Path(str(token_file)).read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("Mímir auth token file is not readable: %s", token_file)
            return None
        if token:
            return token
    return None


def mimir_workload_platform_defaults(settings: MimirClientHost) -> tuple[str | None, str | None]:
    """Workload-auth fallbacks for Mímir instances from ``gateway.platform``.

    Config-first (``.claude/rules/config-first.md``): when a Mímir instance
    uses ``type: workload`` auth without an explicit ``token_file`` /
    ``exchange_url``, the values configured for the platform tools are the
    canonical source. Returns ``(token_file, exchange_url)`` — either may be
    ``None`` when the platform section is disabled or empty.
    """
    platform = settings.gateway.platform
    if not platform.enabled:
        # A flock persona carries no platform block at all, so this returned
        # (None, None), the Mímir adapter got no exchange_url, and it sent
        # every request to the shared Mímir with no Authorization header —
        # surfacing as a bare 401 that looked like a missing credential rather
        # than missing config. The runtime does advertise both values; prefer
        # them over giving up.
        return runtime_workload_defaults()
    token_file = platform.workload_token_file or None
    exchange_url = platform.workload_exchange_url or None
    if not exchange_url and platform.base_url:
        exchange_url = f"{platform.base_url.rstrip('/')}/api/v1/tokens/workload/exchange"
    if not exchange_url or not token_file:
        env_token_file, env_exchange_url = runtime_workload_defaults()
        token_file = token_file or env_token_file
        exchange_url = exchange_url or env_exchange_url
    return token_file, exchange_url


def runtime_workload_defaults() -> tuple[str | None, str | None]:
    """Workload token file and exchange URL advertised by the runtime.

    Bootstrap wiring injected into every session container, not application
    configuration — the same env the workload identity adapters already read.
    """
    token_file = os.environ.get("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", "").strip() or None
    exchange_url = os.environ.get(_WORKLOAD_EXCHANGE_URL_ENV, "").strip() or None
    return token_file, exchange_url


def build_mimir_auth(settings: MimirClientHost, auth_config: Any) -> Any:
    """Build a MimirAuth domain object from a MimirAuthConfig section."""
    from mimir.domain import MimirAuth

    token_file = auth_config.token_file
    exchange_url = auth_config.exchange_url
    if auth_config.type == "workload":
        fallback_token_file, fallback_exchange_url = mimir_workload_platform_defaults(settings)
        token_file = token_file or fallback_token_file
        exchange_url = exchange_url or fallback_exchange_url
    return MimirAuth(
        type=auth_config.type,
        token=resolve_mimir_auth_token(auth_config) if auth_config.type == "bearer" else None,
        token_file=token_file,
        exchange_url=exchange_url,
        audiences=tuple(auth_config.audiences),
        trust_domain=auth_config.trust_domain,
        token_refresh_margin_seconds=auth_config.token_refresh_margin_seconds,
    )


def build_mimir(settings: MimirClientHost) -> Any:
    """Build the Mímir adapter from config, or None if disabled."""
    if not settings.mimir.enabled:
        return None

    if settings.mimir.instances:
        from mimir.adapters.composite import CompositeMimirAdapter
        from mimir.connections import resolve_mimir_connection
        from mimir.domain import MimirMount, WriteRouting

        mounts: list[Any] = []
        for inst in settings.mimir.instances:
            port = resolve_mimir_connection(
                adapter=inst.adapter,
                kwargs=inst.kwargs,
                secret_kwargs_env=inst.secret_kwargs_env,
                path=inst.path,
                url=inst.url,
                environment_id=settings.environment.id,
                auth=build_mimir_auth(settings, inst.auth) if inst.auth is not None else None,
            )
            mounts.append(
                MimirMount(
                    name=inst.name,
                    port=port,
                    role=inst.role,
                    read_priority=inst.read_priority,
                    categories=inst.categories,
                )
            )

        if not mounts:
            return None

        wr = settings.mimir.write_routing
        routing = WriteRouting(
            rules=[(r["prefix"], r["mounts"]) for r in wr.rules],
            default=wr.default,
        )
        retry = settings.mimir.read_retry
        return CompositeMimirAdapter(
            mounts=mounts,
            write_routing=routing,
            read_retry_max_seconds=retry.max_seconds,
            read_retry_initial_backoff_seconds=retry.initial_backoff_seconds,
            read_retry_max_backoff_seconds=retry.max_backoff_seconds,
        )

    # Single local instance
    from mimir.adapters.markdown import MarkdownMimirAdapter

    return MarkdownMimirAdapter(root=settings.mimir.path)
